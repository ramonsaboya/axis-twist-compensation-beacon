# Axis Twist Compensation Beacon
#
# Automates axis twist compensation calibration using Beacon's
# BEACON_OFFSET_COMPARE command, which measures the offset between
# contact (touch) and proximity (scan) probing at each point.
#
# Copyright (C) 2024-2026
# This file may be distributed under the terms of the GNU GPLv3 license.

from . import axis_twist_compensation


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

        # Clear existing compensation for this axis so it doesn't
        # interfere with calibration measurements
        self.axis_comp.clear_compensations(axis)

        # Perform calibration using BEACON_OFFSET_COMPARE
        deltas = self._calibrate(
            gcmd, nozzle_points, speed, horizontal_move_z)

        # Convert deltas to compensation values
        # BEACON_OFFSET_COMPARE delta = contact_z - proximity_z
        # Klipper expects proximity_z - contact_z, so negate
        results = [-d for d in deltas]
        avg = sum(results) / len(results)
        normalized = [avg - r for r in results]

        # Save results
        self._save_results(axis, nozzle_points, normalized, gcmd)

        # Update live compensation
        self._apply_live_compensation(axis, nozzle_points, normalized)

        # Report
        values_str = ', '.join(["%.6f" % v for v in normalized])
        variance = sum((v - avg) ** 2 for v in results) / len(results)
        stddev = variance ** 0.5
        max_deviation = max(abs(v) for v in normalized)
        gcmd.respond_info(
            "AXIS_TWIST_COMPENSATION_BEACON: Calibration complete!\n"
            "  Axis: %s\n"
            "  Points: %d\n"
            "  Offsets: %s\n"
            "  Mean z_offset: %.6f\n"
            "  Standard deviation: %.6f\n"
            "  Max deviation: %.6f\n"
            "  State saved for current session. Run SAVE_CONFIG "
            "to persist." % (axis, len(normalized), values_str, avg,
                             stddev, max_deviation))

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

    def _calibrate(self, gcmd, nozzle_points, speed, horizontal_move_z):
        """Run BEACON_OFFSET_COMPARE at each calibration point."""
        toolhead = self.printer.lookup_object('toolhead')
        deltas = []

        for i, (nx, ny) in enumerate(nozzle_points):
            gcmd.respond_info(
                "AXIS_TWIST_COMPENSATION_BEACON: Probing point "
                "%d of %d (%.1f, %.1f)"
                % (i + 1, len(nozzle_points), nx, ny))

            # Move to safe Z, then to calibration point
            toolhead.manual_move([None, None, horizontal_move_z], speed)
            toolhead.manual_move([nx, ny, None], speed)

            # Run BEACON_OFFSET_COMPARE — it handles contact probe,
            # offset move, and proximity reading internally
            offset_gcmd = self.gcode.create_gcode_command(
                "BEACON_OFFSET_COMPARE", "BEACON_OFFSET_COMPARE", {})
            self.beacon.cmd_BEACON_OFFSET_COMPARE(offset_gcmd)

            delta = self.beacon.last_offset_result["delta"]
            deltas.append(delta)

            gcmd.respond_info(
                "  Delta: %.6f (%.1f um)" % (delta, delta * 1000))

        return deltas

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
