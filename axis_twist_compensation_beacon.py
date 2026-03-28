# Axis Twist Compensation Beacon
#
# Automates axis twist compensation calibration using Beacon's
# proximity (scan) and contact (touch) probing modes, eliminating
# the need for manual paper-test nozzle probing.
#
# Copyright (C) 2024-2026
# This file may be distributed under the terms of the GNU GPLv3 license.

from . import axis_twist_compensation

DEFAULT_CONTACT_SAMPLES = 3
DEFAULT_RETRACT_DIST = 2.0


class AxisTwistCompensationBeacon:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')

        # Configuration (falls back to stock Klipper defaults)
        self.speed = config.getfloat(
            'speed', axis_twist_compensation.DEFAULT_SPEED)
        self.horizontal_move_z = config.getfloat(
            'horizontal_move_z',
            axis_twist_compensation.DEFAULT_HORIZONTAL_MOVE_Z)
        self.probe_speed = config.getfloat('probe_speed', None)
        self.contact_samples = config.getint(
            'contact_samples', DEFAULT_CONTACT_SAMPLES)
        self.retract_dist = config.getfloat(
            'retract_dist', DEFAULT_RETRACT_DIST)

        # Internal state — populated on klippy:connect
        self.beacon = None
        self.axis_comp = None

        # Register event handler to wait for all objects to load
        self.printer.register_event_handler(
            "klippy:connect", self._handle_connect)

        # Register G-Code command
        self.gcode.register_command(
            'AXIS_TWIST_COMPENSATION_BEACON',
            self.cmd_AXIS_TWIST_COMPENSATION_BEACON,
            desc=self.cmd_AXIS_TWIST_COMPENSATION_BEACON_help)

    cmd_AXIS_TWIST_COMPENSATION_BEACON_help = (
        "Calibrate axis twist compensation automatically using "
        "Beacon contact (touch) and proximity (scan) probing"
    )

    def _handle_connect(self):
        # Look up the Beacon probe object (no import needed —
        # Klipper's printer.lookup_object resolves it at runtime)
        self.beacon = self.printer.lookup_object('beacon', None)
        if self.beacon is None:
            raise self.printer.config_error(
                "axis_twist_compensation_beacon requires "
                "[beacon] to be configured")

        # Look up axis_twist_compensation
        self.axis_comp = self.printer.lookup_object(
            'axis_twist_compensation', None)
        if self.axis_comp is None:
            raise self.printer.config_error(
                "axis_twist_compensation_beacon requires "
                "[axis_twist_compensation] to be configured")

    def cmd_AXIS_TWIST_COMPENSATION_BEACON(self, gcmd):
        # Parse parameters
        axis = gcmd.get('AXIS', 'X').upper()
        sample_count = gcmd.get_int(
            'SAMPLE_COUNT', axis_twist_compensation.DEFAULT_SAMPLE_COUNT)
        speed = gcmd.get_float('SPEED', self.speed)
        horizontal_move_z = gcmd.get_float(
            'HORIZONTAL_MOVE_Z', self.horizontal_move_z)
        probe_speed = gcmd.get_float(
            'PROBE_SPEED', self.probe_speed or self.beacon.speed)
        contact_samples = gcmd.get_int(
            'CONTACT_SAMPLES', self.contact_samples)
        retract_dist = gcmd.get_float('RETRACT_DIST', self.retract_dist)

        if sample_count < 2:
            raise gcmd.error("SAMPLE_COUNT must be at least 2")

        if axis not in ('X', 'Y'):
            raise gcmd.error(
                "AXIS must be 'X' or 'Y', got '%s'" % axis)

        # Verify homed
        toolhead = self.printer.lookup_object('toolhead')
        curtime = self.printer.get_reactor().monotonic()
        kin_status = toolhead.get_status(curtime)
        if 'xyz' not in kin_status['homed_axes']:
            raise gcmd.error(
                "Must home all axes before running "
                "AXIS_TWIST_COMPENSATION_BEACON")

        # Calculate calibration points
        nozzle_points = self._get_calibration_points(
            axis, sample_count, gcmd)

        gcmd.respond_info(
            "AXIS_TWIST_COMPENSATION_BEACON: Starting %s axis "
            "calibration with %d points" % (axis, len(nozzle_points)))

        # Perform calibration
        results = self._calibrate(
            gcmd, nozzle_points, speed, horizontal_move_z,
            probe_speed, contact_samples, retract_dist)

        # Normalize results (subtract mean)
        avg = sum(results) / len(results)
        normalized = [avg - r for r in results]

        # Save results
        self._save_results(axis, nozzle_points, normalized, gcmd)

        # Update live compensation
        self._apply_live_compensation(axis, nozzle_points, normalized)

        # Report
        values_str = ', '.join(["%.6f" % v for v in normalized])
        gcmd.respond_info(
            "AXIS_TWIST_COMPENSATION_BEACON: Calibration complete!\n"
            "  Axis: %s\n"
            "  Points: %d\n"
            "  Offsets: %s\n"
            "  Mean z_offset: %.6f\n"
            "  State saved for current session. Run SAVE_CONFIG "
            "to persist." % (axis, len(normalized), values_str, avg))

    def _get_calibration_points(self, axis, sample_count, gcmd):
        """Calculate the nozzle positions for calibration."""
        points = []

        if axis == 'X':
            start_x = self.axis_comp.calibrate_start_x
            end_x = self.axis_comp.calibrate_end_x
            y = self.axis_comp.calibrate_y

            if start_x is None or end_x is None or y is None:
                raise gcmd.error(
                    "AXIS_TWIST_COMPENSATION_BEACON for X axis requires "
                    "calibrate_start_x, calibrate_end_x, and calibrate_y "
                    "to be defined in [axis_twist_compensation]")

            x_range = end_x - start_x
            interval = x_range / (sample_count - 1)

            for i in range(sample_count):
                x = start_x + i * interval
                points.append((x, y))

        elif axis == 'Y':
            start_y = self.axis_comp.calibrate_start_y
            end_y = self.axis_comp.calibrate_end_y
            x = self.axis_comp.calibrate_x

            if start_y is None or end_y is None or x is None:
                raise gcmd.error(
                    "AXIS_TWIST_COMPENSATION_BEACON for Y axis requires "
                    "calibrate_start_y, calibrate_end_y, and calibrate_x "
                    "to be defined in [axis_twist_compensation]")

            y_range = end_y - start_y
            interval = y_range / (sample_count - 1)

            for i in range(sample_count):
                y = start_y + i * interval
                points.append((x, y))

        return points

    def _calibrate(self, gcmd, nozzle_points, speed, horizontal_move_z,
                   probe_speed, contact_samples, retract_dist):
        """Run the calibration sequence at each point."""
        toolhead = self.printer.lookup_object('toolhead')
        results = []

        # Get Beacon's XY offset from the nozzle so we can position
        # the proximity sensor directly over the calibration point.
        probe_offsets = self.beacon.get_offsets()
        x_offset = probe_offsets[0]
        y_offset = probe_offsets[1]

        for i, (nx, ny) in enumerate(nozzle_points):
            gcmd.respond_info(
                "AXIS_TWIST_COMPENSATION_BEACON: Probing point "
                "%d of %d (%.1f, %.1f)"
                % (i + 1, len(nozzle_points), nx, ny))

            # --- Proximity (scan) probe ---
            # Move the nozzle so the probe sensor is over the
            # calibration point (nozzle position = point - offset)
            self._move(toolhead, None, None, horizontal_move_z, speed)
            self._move(toolhead,
                       nx - x_offset, ny - y_offset, None, speed)

            proximity_z = self._probe_proximity(gcmd)

            gcmd.respond_info(
                "  Proximity (scan) Z: %.6f" % proximity_z)

            # --- Contact (touch) probe ---
            # Move nozzle directly to the calibration point
            self._move(toolhead, None, None, horizontal_move_z, speed)
            self._move(toolhead, nx, ny, None, speed)

            contact_z = self._probe_contact(
                gcmd, probe_speed, contact_samples, retract_dist)

            gcmd.respond_info(
                "  Contact (touch) Z: %.6f" % contact_z)

            # The twist error is the difference between what the
            # proximity probe reads and what the nozzle actually
            # touches at. This captures the Z offset variation caused
            # by rail twist at this X/Y position.
            twist_error = proximity_z - contact_z
            results.append(twist_error)

            gcmd.respond_info(
                "  Twist error: %.6f" % twist_error)

            # Move back to safe height
            self._move(toolhead, None, None, horizontal_move_z, speed)

        return results

    def _probe_proximity(self, gcmd):
        """Run a single Beacon proximity (scan/induction) probe."""
        beacon = self.beacon

        # Use Beacon's internal probe method with proximity mode
        old_method = beacon.default_probe_method
        beacon.default_probe_method = 'proximity'
        try:
            beacon._start_streaming()
            try:
                result = beacon._probe(beacon.speed)
            finally:
                beacon._stop_streaming()
        finally:
            beacon.default_probe_method = old_method

        return result[2]

    def _probe_contact(self, gcmd, probe_speed, sample_count,
                       retract_dist):
        """Run Beacon contact (touch/nozzle) probing with averaging."""
        beacon = self.beacon
        toolhead = self.printer.lookup_object('toolhead')
        samples = []

        lift_speed = beacon.get_lift_speed()

        beacon._start_streaming()
        try:
            beacon.mcu_contact_probe.activate_gcode \
                .run_gcode_from_command()
            try:
                for s in range(sample_count):
                    pos = beacon._probe_contact(probe_speed)
                    samples.append(pos[2])
                    # Retract between samples
                    posxy = toolhead.get_position()[:2]
                    toolhead.manual_move(
                        posxy + [pos[2] + retract_dist], lift_speed)
            finally:
                beacon.mcu_contact_probe.deactivate_gcode \
                    .run_gcode_from_command()
        finally:
            beacon._stop_streaming()

        # Return mean of samples
        return sum(samples) / len(samples)

    def _move(self, toolhead, x, y, z, speed):
        """Move the toolhead, None values = don't change that axis."""
        toolhead.manual_move([x, y, z], speed)

    def _save_results(self, axis, points, compensations, gcmd):
        """Save calibration results to the config file."""
        configfile = self.printer.lookup_object('configfile')
        config_name = 'axis_twist_compensation'
        values_str = ', '.join(["%.6f" % v for v in compensations])

        if axis == 'X':
            configfile.set(config_name, 'z_compensations', values_str)
            configfile.set(config_name, 'compensation_start_x',
                           points[0][0])
            configfile.set(config_name, 'compensation_end_x',
                           points[-1][0])
        elif axis == 'Y':
            configfile.set(config_name, 'zy_compensations', values_str)
            configfile.set(config_name, 'compensation_start_y',
                           points[0][1])
            configfile.set(config_name, 'compensation_end_y',
                           points[-1][1])

    def _apply_live_compensation(self, axis, points, compensations):
        """Apply the compensation to the live session."""
        if axis == 'X':
            self.axis_comp.z_compensations = compensations
            self.axis_comp.compensation_start_x = points[0][0]
            self.axis_comp.compensation_end_x = points[-1][0]
        elif axis == 'Y':
            self.axis_comp.zy_compensations = compensations
            self.axis_comp.compensation_start_y = points[0][1]
            self.axis_comp.compensation_end_y = points[-1][1]


def load_config(config):
    return AxisTwistCompensationBeacon(config)
