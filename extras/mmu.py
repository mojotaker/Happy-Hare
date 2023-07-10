# Happy Hare MMU Software
# Main module
#
# Copyright (C) 2022  moggieuk#6538 (discord)
#                     moggieuk@hotmail.com
#
# Inspired by original ERCF software
# Enraged Rabbit Carrot Feeder Project           Copyright (C) 2021  Ette
#
# (\_/)
# ( *,*)
# (")_(") MMU Ready
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
import logging, logging.handlers, threading, queue, time
import textwrap, math, os.path, re, json
from random import randint
import chelper

# Forward all messages through a queue (polled by background thread)
class QueueHandler(logging.Handler):
    def __init__(self, queue):
        logging.Handler.__init__(self)
        self.queue = queue

    def emit(self, record):
        try:
            self.format(record)
            record.msg = record.message
            record.args = None
            record.exc_info = None
            self.queue.put_nowait(record)
        except Exception:
            self.handleError(record)

# Poll log queue on background thread and log each message to logfile
class QueueListener(logging.handlers.TimedRotatingFileHandler):
    def __init__(self, filename):
        logging.handlers.TimedRotatingFileHandler.__init__(
            self, filename, when='midnight', backupCount=5)
        self.bg_queue = queue.Queue()
        self.bg_thread = threading.Thread(target=self._bg_thread)
        self.bg_thread.start()

    def _bg_thread(self):
        while True:
            record = self.bg_queue.get(True)
            if record is None:
                break
            self.handle(record)

    def stop(self):
        self.bg_queue.put_nowait(None)
        self.bg_thread.join()

# Class to improve formatting of multi-line messages
class MultiLineFormatter(logging.Formatter):
    def format(self, record):
        indent = ' ' * 9
        lines = super(MultiLineFormatter, self).format(record)
        return lines.replace('\n', '\n' + indent)

# Mmu exception error class
class MmuError(Exception):
    pass

# Main klipper module
class Mmu:
    BOOT_DELAY = 1.5            # Delay before running bootup tasks

    LONG_MOVE_THRESHOLD = 70.   # This is also the initial move to load past encoder

    # Calibration steps
    CALIBRATED_GEAR     = 0b00001
    CALIBRATED_ENCODER  = 0b00010
    CALIBRATED_SELECTOR = 0b00100
    CALIBRATED_BOWDEN   = 0b01000
    CALIBRATED_GATES    = 0b10000
    CALIBRATED_ALL      = 0b01111 # Calibrated gates is optional

    SERVO_MOVE_STATE = 2 # NEW V2
    SERVO_DOWN_STATE = 1
    SERVO_UP_STATE = 0
    SERVO_UNKNOWN_STATE = -1

    TOOL_UNKNOWN = -1
    TOOL_BYPASS = -2

    GATE_UNKNOWN = -1
    GATE_EMPTY = 0
    GATE_AVAILABLE = 1 # Available to load from either buffer or spool
    GATE_AVAILABLE_FROM_BUFFER = 2

    LOADED_STATUS_UNKNOWN = -1
    LOADED_STATUS_UNLOADED = 0
    LOADED_STATUS_PARTIAL_BEFORE_ENCODER = 1
    LOADED_STATUS_PARTIAL_PAST_ENCODER = 2
    LOADED_STATUS_PARTIAL_IN_BOWDEN = 3
    LOADED_STATUS_PARTIAL_END_OF_BOWDEN = 4
    LOADED_STATUS_PARTIAL_HOMED_EXTRUDER = 5
    LOADED_STATUS_PARTIAL_HOMED_SENSOR = 6
    LOADED_STATUS_PARTIAL_IN_EXTRUDER = 7
    LOADED_STATUS_FULL = 8

    DIRECTION_LOAD = 1
    DIRECTION_UNLOAD = -1

    ACTION_IDLE = 0
    ACTION_LOADING = 1
    ACTION_LOADING_EXTRUDER = 2
    ACTION_UNLOADING = 3
    ACTION_UNLOADING_EXTRUDER = 4
    ACTION_FORMING_TIP = 5
    ACTION_HEATING = 6
    ACTION_CHECKING = 7
    ACTION_HOMING = 8
    ACTION_SELECTING = 9

    # Standard endstop or pseudo endstop names
    EXTRUDER_COLLISION_ENDSTOP = "collision"
    EXTRUDER_TOUCH_ENDSTOP = "mmu_ext_touch"
    GEAR_TOUCH_ENDSTOP = "mmu_gear_touch"
    SELECTOR_TOUCH_ENDSTOP = "mmu_sel_touch"
    SELECTOR_HOME_ENDSTOP = "mmu_sel_home"

    # Vendor MMU's supported
    VENDOR_ERCF = "ERCF"
    VENDOR_TRADRACK = "Tradrack" # In progress
    VENDOR_PRUSA = "Prusa" # In progress

    # mmu_vars.cfg variables
    VARS_MMU_CALIB_CLOG_LENGTH      = "mmu_calibration_clog_length"
    VARS_MMU_ENABLE_ENDLESS_SPOOL   = "mmu_state_enable_endless_spool"
    VARS_MMU_ENDLESS_SPOOL_GROUPS   = "mmu_state_endless_spool_groups"
    VARS_MMU_TOOL_TO_GATE_MAP       = "mmu_state_tool_to_gate_map"
    VARS_MMU_GATE_STATUS            = "mmu_state_gate_status"
    VARS_MMU_GATE_MATERIAL          = "mmu_state_gate_material"
    VARS_MMU_GATE_COLOR             = "mmu_state_gate_color"
    VARS_MMU_GATE_SELECTED          = "mmu_state_gate_selected"
    VARS_MMU_TOOL_SELECTED          = "mmu_state_tool_selected"
    VARS_MMU_LOADED_STATUS          = "mmu_state_loaded_status"
    VARS_MMU_CALIB_BOWDEN_LENGTH    = "mmu_calibration_bowden_length"
    VARS_MMU_CALIB_PREFIX           = "mmu_calibration_"
    VARS_MMU_GATE_STATISTICS_PREFIX = "mmu_statistics_gate_"
    VARS_MMU_SWAP_STATISTICS        = "mmu_statistics_swaps"
    VARS_MMU_SELECTOR_OFFSETS       = "mmu_selector_offsets"
    VARS_MMU_SELECTOR_BYPASS        = "mmu_selector_bypass"
    VARS_MMU_ENCODER_RESOLUTION     = "mmu_encoder_resolution"
    VARS_MMU_GEAR_ROTATION_DISTANCE = "mmu_gear_rotation_distance"

    EMPTY_GATE_STATS_ENTRY = {'pauses': 0, 'loads': 0, 'load_distance': 0.0, 'load_delta': 0.0, 'unloads': 0, 'unload_distance': 0.0, 'unload_delta': 0.0, 'servo_retries': 0, 'load_failures': 0, 'unload_failures': 0}

    W3C_COLORS = ['aliceblue', 'antiquewhite', 'aqua', 'aquamarine', 'azure', 'beige', 'bisque', 'black', 'blanchedalmond', 'blue', 'blueviolet',
                  'brown', 'burlywood', 'cadetblue', 'chartreuse', 'chocolate', 'coral', 'cornflowerblue', 'cornsilk', 'crimson', 'cyan', 'darkblue',
                  'darkcyan', 'darkgoldenrod', 'darkgray', 'darkgreen', 'darkgrey', 'darkkhaki', 'darkmagenta', 'darkolivegreen', 'darkorange',
                  'darkorchid', 'darkred', 'darksalmon', 'darkseagreen', 'darkslateblue', 'darkslategray', 'darkslategrey', 'darkturquoise', 'darkviolet',
                  'deeppink', 'deepskyblue', 'dimgray', 'dimgrey', 'dodgerblue', 'firebrick', 'floralwhite', 'forestgreen', 'fuchsia', 'gainsboro',
                  'ghostwhite', 'gold', 'goldenrod', 'gray', 'green', 'greenyellow', 'grey', 'honeydew', 'hotpink', 'indianred', 'indigo', 'ivory',
                  'khaki', 'lavender', 'lavenderblush', 'lawngreen', 'lemonchiffon', 'lightblue', 'lightcoral', 'lightcyan', 'lightgoldenrodyellow',
                  'lightgray', 'lightgreen', 'lightgrey', 'lightpink', 'lightsalmon', 'lightseagreen', 'lightskyblue', 'lightslategray', 'lightslategrey',
                  'lightsteelblue', 'lightyellow', 'lime', 'limegreen', 'linen', 'magenta', 'maroon', 'mediumaquamarine', 'mediumblue', 'mediumorchid',
                  'mediumpurple', 'mediumseagreen', 'mediumslateblue', 'mediumspringgreen', 'mediumturquoise', 'mediumvioletred', 'midnightblue',
                  'mintcream', 'mistyrose', 'moccasin', 'navajowhite', 'navy', 'oldlace', 'olive', 'olivedrab', 'orange', 'orangered', 'orchid',
                  'palegoldenrod', 'palegreen', 'paleturquoise', 'palevioletred', 'papayawhip', 'peachpuff', 'peru', 'pink', 'plum', 'powderblue',
                  'purple', 'rebeccapurple', 'red', 'rosybrown', 'royalblue', 'saddlebrown', 'salmon', 'sandybrown', 'seagreen', 'seashell', 'sienna',
                  'silver', 'skyblue', 'slateblue', 'slategray', 'slategrey', 'snow', 'springgreen', 'steelblue', 'tan', 'teal', 'thistle', 'tomato',
                  'turquoise', 'violet', 'wheat', 'white', 'whitesmoke', 'yellow', 'yellowgreen']

    UPGRADE_REMINDER = "Did you upgrade? Run Happy Hare './install.sh' again to fix configuration files and/or read https://github.com/moggieuk/Happy-Hare/blob/main/doc/UPGRADE.md"

    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.estimated_print_time = None
        self.last_selector_move_time = 0
        self.gear_stepper_run_current = -1
        self.calibration_status = 0b0
        self.calibrated_bowden_length = -1
        self.ref_gear_rotation_distance = 1.
        self.printer.register_event_handler('klippy:connect', self.handle_connect)
        self.printer.register_event_handler("klippy:disconnect", self.handle_disconnect)
        self.printer.register_event_handler("klippy:ready", self.handle_ready)

        # MMU hardware (steppers, servo, encoder and optional toolhead sensor)
        self.selector_stepper = self.gear_stepper = self.mmu_extruder_stepper = self.toolhead_sensor = self.encoder_sensor = self.servo = None

        # Specific vendor build parameters / tuning
        self.mmu_vendor = config.get('mmu_vendor', self.VENDOR_ERCF)
        self.mmu_version_string = config.get('mmu_version', "1.1")
        self.mmu_version = float(re.sub("[^0-9.]", "", self.mmu_version_string))
        bmg_circ = 23.
        if self.mmu_vendor.lower() == self.VENDOR_ERCF.lower():
            if self.mmu_version >= 2.0:
                self.cad_gate0_pos = 4.0
                self.cad_gate_width = 23.0
                self.cad_bypass_offset = 5.7
                self.cad_last_gate_offset = 19.2
                self.encoder_min_resolution = bmg_circ / (2 * 12) # Binky 12 tooth disc with BMG gear
            else: # V1.1
                self.cad_gate0_pos = 4.2
                if "t" in self.mmu_version_string:
                    self.cad_gate_width = 23. # Triple Decky is wider filament block
                else:
                    self.cad_gate_width = 21.
                self.cad_bypass_offset = 0.
                if "s" in self.mmu_version_string:
                    self.cad_last_gate_offset = 1.2 # Springy has additional bump stops
                else:
                    self.cad_last_gate_offset = 2.0
                self.cad_block_width = 5.
                self.cad_bypass_block_width = 6.
                self.cad_bypass_block_delta = 9.
                if "b" in self.mmu_version_string:
                    self.encoder_min_resolution = bmg_circ / (2 * 12) # Binky 12 tooth disc with BMG gear
                else:
                    self.encoder_min_resolution = bmg_circ / (2 * 17) # Original 17 tooth BMG gear
            self.cal_max_gates = 12
            self.cal_tolerance = 5.0
        else:
            raise self.config.error("Support for non-ERCF systems is comming soon!")

        # Still allow some 'cad' parameters to be customized, possibly temporary
        self.cad_gate_width = config.getfloat('cad_gate_width', self.cad_gate_width)
        self.cad_bypass_block_width = config.getfloat('cad_bypass_block_width', self.cad_bypass_block_width)
        self.encoder_min_resolution = config.getfloat('encoder_min_resolution', self.encoder_min_resolution)

        # The threshold (mm) that determines real encoder movement (set to 1.5 pulses of encoder. i.e. allow one error pulse)
        self.encoder_min = 1.5 * self.encoder_min_resolution

        # Printer interaction config
        self.extruder_name = config.get('extruder', 'extruder')
        self.timeout_pause = config.getint('timeout_pause', 72000)
        self.timeout_unlock = config.getint('timeout_unlock', -1)
        self.disable_heater = config.getint('disable_heater', 600)
        self.min_temp_extruder = config.getfloat('min_temp_extruder', 180.)

        # User MMU setup
        self.num_gates = config.getint('num_gates')
        self.selector_offsets = list(config.getfloatlist('selector_offsets', []))
        self.bypass_offset = config.getfloat('bypass_selector', 0)
        self.default_tool_to_gate_map = list(config.getintlist('tool_to_gate_map', []))
        self.default_gate_status = list(config.getintlist('gate_status', []))
        self.default_gate_material = list(config.getlist('gate_material', []))
        self.default_gate_color = list(config.getlist('gate_color', []))

        # Software behavior options
        self.persistence_level = config.getint('persistence_level', 0, minval=0, maxval=4)
        self.z_hop_height = config.getfloat('z_hop_height', 5., minval=0.)
        self.z_hop_speed = config.getfloat('z_hop_speed', 15., minval=1.)
        self.num_moves = config.getint('num_moves', 1, minval=1)
        self.unload_buffer = config.getfloat('unload_buffer', 30., minval=15.)
        self.parking_distance = config.getfloat('parking_distance', 23., minval=12., maxval=60.)
        self.encoder_move_step_size = config.getfloat('encoder_move_step_size', 15., minval=5., maxval=25.)
        self.load_encoder_retries = config.getint('load_encoder_retries', 2, minval=1, maxval=5)

        # Homing, loading and unloading controls
        self.apply_bowden_correction = config.getint('apply_bowden_correction', 0, minval=0, maxval=1)
        self.load_bowden_tolerance = config.getfloat('load_bowden_tolerance', 10., minval=1.)
        self.unload_bowden_tolerance = config.getfloat('unload_bowden_tolerance', self.load_bowden_tolerance, minval=1.)
        self.home_to_extruder = config.getint('home_to_extruder', 0, minval=0, maxval=1)
        self.extruder_homing_endstop = config.get('extruder_homing_endstop', self.EXTRUDER_COLLISION_ENDSTOP)
        self.extruder_homing_max = config.getfloat('extruder_homing_max', 50., above=10.)
        self.extruder_collision_homing_step = config.getint('extruder_collision_homing_step', 3, minval=2, maxval=5)
        self.toolhead_homing_max = config.getfloat('toolhead_homing_max', 20., minval=0.)
        self.transition_length = config.getfloat('transition_length', 10., minval=0., maxval=100.)
        self.delay_servo_release = config.getfloat('delay_servo_release', 2., minval=0., maxval=5.)
        self.home_position_to_nozzle = config.getfloat('home_position_to_nozzle', minval=5.) # Legacy, separate measures below are preferred
        self.extruder_to_nozzle = config.getfloat('extruder_to_nozzle', 0., minval=5.) # For sensorless
        self.sensor_to_nozzle = config.getfloat('sensor_to_nozzle', 0., minval=5.) # For toolhead sensor
        self.ignore_extruder_load_error = config.getint('ignore_extruder_load_error', 0, minval=0, maxval=1)
        # Gear/Extruder synchronization controls
        self.sync_to_extruder = config.getint('sync_to_extruder', 0, minval=0, maxval=1)
        self.sync_load_extruder = config.getint('sync_load_extruder', 0, minval=0, maxval=1)
        self.sync_unload_extruder = config.getint('sync_unload_extruder', 0, minval=0, maxval=1)
        self.sync_form_tip = config.getint('sync_form_tip', 0, minval=0, maxval=1)

        # Servo control
        self.servo_down_angle = config.getfloat('servo_down_angle')
        self.servo_up_angle = config.getfloat('servo_up_angle')
        self.servo_move_angle = config.getfloat('servo_move_angle', self.servo_up_angle)
        self.servo_duration = config.getfloat('servo_duration', 0.2, minval=0.1)

        # TMC current control
        self.extruder_homing_current = config.getint('extruder_homing_current', 50, minval=10, maxval=100)
        self.extruder_form_tip_current = config.getint('extruder_form_tip_current', 100, minval=100, maxval=150)
        self.sync_gear_current = config.getint('sync_gear_current', 50, minval=10, maxval=100)

        # Speeds and accelaration
        self.gear_short_move_speed = config.getfloat('gear_short_move_speed', 60., minval=1.)
        self.gear_from_buffer_speed = config.getfloat('gear_from_buffer_speed', 150., minval=1.)
        self.gear_from_spool_speed = config.getfloat('gear_from_spool_speed', 60, minval=1.)
        self.gear_homing_speed = config.getfloat('gear_homing_speed', 50, minval=1.)
        self.extruder_homing_speed = config.getfloat('extruder_homing_speed', 15, minval=1.)
        self.extruder_load_speed = config.getfloat('extruder_load_speed', 15, minval=1.)
        self.extruder_unload_speed = config.getfloat('extruder_unload_speed', 15, minval=1.)
        self.extruder_sync_load_speed = config.getfloat('extruder_sync_load_speed', 15., minval=1.)
        self.extruder_sync_unload_speed = config.getfloat('extruder_sync_unload_speed', 15., minval=1.)
        self.extruder_accel = config.getfloat('extruder_accel', 1000, minval=100.) # For when not synced to toolhead
        self.gear_buzz_accel = config.getfloat('gear_buzz_accel', 2000, minval=100.)
        # Selector speeds
        self.selector_move_speed = config.getfloat('selector_move_speed', 200, minval=1.)
        self.selector_homing_speed = config.getfloat('selector_homing_speed', 100, minval=1.)
        self.selector_touch_speed = config.getfloat('selector_touch_speed', 60, minval=1.)

        # Optional features
        self.enable_selector_touch = config.getint('enable_selector_touch', 1, minval=0, maxval=1)
        self.enable_clog_detection = config.getint('enable_clog_detection', 2, minval=0, maxval=2)
        self.default_enable_endless_spool = config.getint('enable_endless_spool', 0, minval=0, maxval=1)
        self.default_endless_spool_groups = list(config.getintlist('endless_spool_groups', []))

        # Logging
        self.log_level = config.getint('log_level', 1, minval=0, maxval=4)
        self.logfile_level = config.getint('logfile_level', 3, minval=-1, maxval=4)
        self.log_statistics = config.getint('log_statistics', 0, minval=0, maxval=1)
        self.log_visual = config.getint('log_visual', 1, minval=0, maxval=2)
        self.startup_status = config.getint('startup_status', 1, minval=0, maxval=2)

        # The following lists are the defaults (when reset) and will be overriden by values in mmu_vars.cfg

        # Endless spool groups
        self.enable_endless_spool = self.default_enable_endless_spool
        if len(self.default_endless_spool_groups) > 0:
            if self.enable_endless_spool == 1 and len(self.default_endless_spool_groups) != self.num_gates:
                raise self.config.error("endless_spool_groups has a different number of values than the number of gates")
        else:
            for i in range(self.num_gates):
                self.default_endless_spool_groups.append(i)
        self.endless_spool_groups = list(self.default_endless_spool_groups)

        # Status (availability of filament) at each gate
        if len(self.default_gate_status) > 0:
            if not len(self.default_gate_status) == self.num_gates:
                raise self.config.error("gate_status has different number of values than the number of gates")
        else:
            for i in range(self.num_gates):
                self.default_gate_status.append(self.GATE_UNKNOWN)
        self.gate_status = list(self.default_gate_status)

        # Filmament material at each gate
        if len(self.default_gate_material) > 0:
            if not len(self.default_gate_material) == self.num_gates:
                raise self.config.error("gate_material has different number of entries than the number of gates")
        else:
            for i in range(self.num_gates):
                self.default_gate_material.append("")
        self.gate_material = list(self.default_gate_material)

        # Filmament color at each gate
        if len(self.default_gate_color) > 0:
            if not len(self.default_gate_color) == self.num_gates:
                raise self.config.error("gate_color has different number of entries than the number of gates")
        else:
            for i in range(self.num_gates):
                self.default_gate_color.append("")
        self.gate_color = list(self.default_gate_color)

        # Tool to gate mapping
        if len(self.default_tool_to_gate_map) > 0:
            if not len(self.default_tool_to_gate_map) == self.num_gates:
                raise self.config.error("tool_to_gate_map has different number of values than the number of gates")
        else:
            for i in range(self.num_gates):
                self.default_tool_to_gate_map.append(i)
        self.tool_to_gate_map = list(self.default_tool_to_gate_map)

        # Initialize state and statistics variables
        self._initialize_state()

        # Logging
        self.queue_listener = None
        self.mmu_logger = None

        # Register GCODE commands
        self.gcode = self.printer.lookup_object('gcode')

        # Logging and Stats
        self.gcode.register_command('MMU_RESET', self.cmd_MMU_RESET, desc = self.cmd_MMU_RESET_help)
        self.gcode.register_command('MMU_STATS', self.cmd_MMU_STATS, desc = self.cmd_MMU_STATS_help)
        self.gcode.register_command('MMU_STATUS', self.cmd_MMU_STATUS, desc = self.cmd_MMU_STATUS_help)

        # Calibration
        self.gcode.register_command('MMU_CALIBRATE_GEAR', self.cmd_MMU_CALIBRATE_GEAR, desc=self.cmd_MMU_CALIBRATE_GEAR_help)
        self.gcode.register_command('MMU_CALIBRATE_ENCODER', self.cmd_MMU_CALIBRATE_ENCODER, desc=self.cmd_MMU_CALIBRATE_ENCODER_help)
        self.gcode.register_command('MMU_CALIBRATE_SELECTOR', self.cmd_MMU_CALIBRATE_SELECTOR, desc = self.cmd_MMU_CALIBRATE_SELECTOR_help)
        self.gcode.register_command('MMU_CALIBRATE_BOWDEN', self.cmd_MMU_CALIBRATE_BOWDEN, desc = self.cmd_MMU_CALIBRATE_BOWDEN_help)
        self.gcode.register_command('MMU_CALIBRATE_GATES', self.cmd_MMU_CALIBRATE_GATES, desc = self.cmd_MMU_CALIBRATE_GATES_help)

        # Servo and motor control
        self.gcode.register_command('MMU_SERVO', self.cmd_MMU_SERVO, desc = self.cmd_MMU_SERVO_help)
        self.gcode.register_command('MMU_MOTORS_OFF', self.cmd_MMU_MOTORS_OFF, desc = self.cmd_MMU_MOTORS_OFF_help)
        self.gcode.register_command('MMU_SYNC_GEAR_MOTOR', self.cmd_MMU_SYNC_GEAR_MOTOR, desc=self.cmd_MMU_SYNC_GEAR_MOTOR_help)

        # Core MMU functionality
        self.gcode.register_command('MMU', self.cmd_MMU, desc = self.cmd_MMU_help)
        self.gcode.register_command('MMU_HELP', self.cmd_MMU_HELP, desc = self.cmd_MMU_HELP_help)
        self.gcode.register_command('MMU_ENCODER', self.cmd_MMU_ENCODER, desc = self.cmd_MMU_ENCODER_help)
        self.gcode.register_command('MMU_HOME', self.cmd_MMU_HOME, desc = self.cmd_MMU_HOME_help)
        self.gcode.register_command('MMU_SELECT', self.cmd_MMU_SELECT, desc = self.cmd_MMU_SELECT_help)
        self.gcode.register_command('MMU_PRELOAD', self.cmd_MMU_PRELOAD, desc = self.cmd_MMU_PRELOAD_help)
        self.gcode.register_command('MMU_SELECT_BYPASS', self.cmd_MMU_SELECT_BYPASS, desc = self.cmd_MMU_SELECT_BYPASS_help)
        self.gcode.register_command('MMU_CHANGE_TOOL', self.cmd_MMU_CHANGE_TOOL, desc = self.cmd_MMU_CHANGE_TOOL_help)
        self.gcode.register_command('MMU_LOAD', self.cmd_MMU_LOAD, desc=self.cmd_MMU_LOAD_help)
        self.gcode.register_command('MMU_EJECT', self.cmd_MMU_EJECT, desc = self.cmd_MMU_EJECT_help)
        self.gcode.register_command('MMU_UNLOCK', self.cmd_MMU_UNLOCK, desc = self.cmd_MMU_UNLOCK_help)
        self.gcode.register_command('MMU_PAUSE', self.cmd_MMU_PAUSE, desc = self.cmd_MMU_PAUSE_help)
        self.gcode.register_command('MMU_RECOVER', self.cmd_MMU_RECOVER, desc = self.cmd_MMU_RECOVER_help)

        # User Setup and Testing
        self.gcode.register_command('MMU_TEST_BUZZ_MOTOR', self.cmd_MMU_TEST_BUZZ_MOTOR, desc=self.cmd_MMU_TEST_BUZZ_MOTOR_help)
        self.gcode.register_command('MMU_TEST_GRIP', self.cmd_MMU_TEST_GRIP, desc = self.cmd_MMU_TEST_GRIP_help)
        self.gcode.register_command('MMU_TEST_LOAD', self.cmd_MMU_TEST_LOAD, desc=self.cmd_MMU_TEST_LOAD_help)
        self.gcode.register_command('MMU_TEST_MOVE', self.cmd_MMU_TEST_MOVE, desc = self.cmd_MMU_TEST_MOVE_help)
        self.gcode.register_command('MMU_TEST_HOMING_MOVE', self.cmd_MMU_TEST_HOMING_MOVE, desc = self.cmd_MMU_TEST_HOMING_MOVE_help)
        self.gcode.register_command('MMU_TEST_TRACKING', self.cmd_MMU_TEST_TRACKING, desc=self.cmd_MMU_TEST_TRACKING_help)
        self.gcode.register_command('MMU_TEST_CONFIG', self.cmd_MMU_TEST_CONFIG, desc = self.cmd_MMU_TEST_CONFIG_help)

        # Soak Testing
        self.gcode.register_command('MMU_SOAKTEST_SELECTOR', self.cmd_MMU_SOAKTEST_SELECTOR, desc = self.cmd_MMU_SOAKTEST_SELECTOR_help)
        self.gcode.register_command('MMU_SOAKTEST_LOAD_SEQUENCE', self.cmd_MMU_SOAKTEST_LOAD_SEQUENCE, desc = self.cmd_MMU_SOAKTEST_LOAD_SEQUENCE_help)

        # Runout, TTG and Endless spool
        self.gcode.register_command('_MMU_ENCODER_RUNOUT', self.cmd_MMU_ENCODER_RUNOUT, desc = self.cmd_MMU_ENCODER_RUNOUT_help)
        self.gcode.register_command('_MMU_ENCODER_INSERT', self.cmd_MMU_ENCODER_INSERT, desc = self.cmd_MMU_ENCODER_INSERT_help)
        self.gcode.register_command('MMU_REMAP_TTG', self.cmd_MMU_REMAP_TTG, desc = self.cmd_MMU_REMAP_TTG_help)
        self.gcode.register_command('MMU_SET_GATE_MAP', self.cmd_MMU_SET_GATE_MAP, desc = self.cmd_MMU_SET_GATE_MAP_help)
        self.gcode.register_command('MMU_ENDLESS_SPOOL', self.cmd_MMU_ENDLESS_SPOOL, desc = self.cmd_MMU_ENDLESS_SPOOL_help)
        self.gcode.register_command('MMU_CHECK_GATES', self.cmd_MMU_CHECK_GATES, desc = self.cmd_MMU_CHECK_GATES_help)

        # We setup MMU hardware during configuration since some hardware like endstop requires
        # configuration during the MCU config phase, which happens before klipper connection
        # This assumes that the hardware configuartion appears before the `[mmu]` section
        # the installer by default already guarantees this order
        self._setup_mmu_hardware(config)

    def _setup_logging(self):
        # Setup background file based logging before logging any messages
        if self.logfile_level >= 0:
            logfile_path = self.printer.start_args['log_file']
            dirname = os.path.dirname(logfile_path)
            if dirname == None:
                mmu_log = '/tmp/mmu.log'
            else:
                mmu_log = dirname + '/mmu.log'
            self._log_debug("mmu_log=%s" % mmu_log)
            self.queue_listener = QueueListener(mmu_log)
            self.queue_listener.setFormatter(MultiLineFormatter('%(asctime)s %(message)s', datefmt='%I:%M:%S'))
            queue_handler = QueueHandler(self.queue_listener.bg_queue)
            self.mmu_logger = logging.getLogger('mmu')
            self.mmu_logger.setLevel(logging.INFO)
            self.mmu_logger.addHandler(queue_handler)

    def _setup_mmu_hardware(self, config):
        self.mmu_hardware = self.printer.lookup_object('mmu_hardware', None)

        # Selector h/w setup ------
        for manual_stepper in self.printer.lookup_objects('manual_mh_stepper'):
            stepper_name = manual_stepper[1].get_steppers()[0].get_name()
            if stepper_name == 'manual_mh_stepper selector_stepper':
                self.selector_stepper = manual_stepper[1]
        if self.selector_stepper is None:
            raise self.config.error("Missing [manual_mh_stepper selector_stepper] section in mmu_hardware.cfg")

        # Find the pyhysical (homing) selector endstop
        self.selector_endstop = self.selector_stepper.get_endstop(self.SELECTOR_HOME_ENDSTOP)
        if self.selector_endstop is None:
            for name in self.selector_stepper.get_endstop_names():
                if not self.selector_stepper.is_endstop_virtual(name):
                    self.selector_endstop = self.selector_stepper.get_endstop(name)
                    break
        if self.selector_endstop is None:
            raise self.config.error("Physical homing endstop not found for selector_stepper")

        # If user didn't configure a default endstop do it here. Mimimizes config differences
        if 'default' not in self.selector_stepper.get_endstop_names():
            self.selector_stepper.activate_endstop(self.SELECTOR_HOME_ENDSTOP)
        self.selector_touch = self.SELECTOR_TOUCH_ENDSTOP in self.selector_stepper.get_endstop_names() and self.enable_selector_touch

        # Gear h/w setup ------
        for manual_stepper in self.printer.lookup_objects('manual_extruder_stepper'):
            stepper_name = manual_stepper[1].get_steppers()[0].get_name()
            if stepper_name == 'manual_extruder_stepper gear_stepper':
                self.gear_stepper = manual_stepper[1]
            if stepper_name == "manual_extruder_stepper extruder":
                self.mmu_extruder_stepper = manual_stepper[1]
        if self.gear_stepper is None:
            raise self.config.error("Missing [manual_extruder_stepper gear_stepper] definition in mmu_hardware.cfg\n%s" % self.UPGRADE_REMINDER)
        if self.mmu_extruder_stepper is None:
            raise self.config.error("Missing [manual_extruder_stepper extruder] definition in mmu_hardware.cfg\n%s" % self.UPGRADE_REMINDER)

        # Optional toolhead sensor (assumed to be after extruder gears)
        self.toolhead_sensor = self.printer.lookup_object("filament_switch_sensor toolhead_sensor", None)
        if self.toolhead_sensor:
            self.toolhead_sensor.runout_helper.runout_pause = False # With MMU this must not pause nor call user defined macros
            toolhead_sensor_pin = self.config.getsection("filament_switch_sensor toolhead_sensor").get("switch_pin")

            # Add toolhead sensor pin as an extra endstop for gear_stepper
            ppins = self.printer.lookup_object('pins')
            ppins.allow_multi_use_pin(toolhead_sensor_pin)
            mcu_endstop = self.gear_stepper._add_endstop(toolhead_sensor_pin, "mmu_toolhead")

            # Finally we might want to home the extruder to toolhead sensor in isolation
            self.mmu_extruder_stepper._add_endstop(toolhead_sensor_pin, "mmu_toolhead", register=False)

        # To allow for homing moves on extruder synced with gear and gear synced with extruder we need
        # each to share each others endstops
        for en in self.mmu_extruder_stepper.get_endstop_names():
            mcu_es = self.mmu_extruder_stepper.get_endstop(en)
            for s in self.gear_stepper.steppers:
                mcu_es.add_stepper(s)
        for en in self.gear_stepper.get_endstop_names():
            mcu_es = self.gear_stepper.get_endstop(en)
            for s in self.mmu_extruder_stepper.steppers:
                mcu_es.add_stepper(s)

        # Get servo and encoder setup -----
        self.servo = self.printer.lookup_object('mmu_servo mmu_servo', None)
        if not self.servo:
            raise self.config.error("Missing [mmu_servo] definition in mmu_hardware.cfg\n%s" % self.UPGRADE_REMINDER)
        self.encoder_sensor = self.printer.lookup_object('mmu_encoder mmu_encoder', None)
        if not self.encoder_sensor:
            raise self.config.error("Missing [mmu_encoder] definition in mmu_hardware.cfg\n%s" % self.UPGRADE_REMINDER)

    def handle_connect(self):
        self._setup_logging()
        self.toolhead = self.printer.lookup_object('toolhead')

        # See if we have a TMC controller capable of current control for filament collision detection and syncing
        # on gear_stepper and tip forming on extruder
        self.selector_tmc = self.gear_tmc = self.extruder_tmc = None
        tmc_chips = ["tmc2209", "tmc2130", "tmc2208", "tmc2660", "tmc5160", "tmc2240"]
        for chip in tmc_chips:
            if self.selector_tmc is None:
                self.selector_tmc = self.printer.lookup_object('%s manual_mh_stepper selector_stepper' % chip, None)
                if self.selector_tmc is not None:
                    self._log_debug("Found %s on selector_stepper. Stallguard 'touch' homing possible." % chip)
            if self.gear_tmc is None:
                self.gear_tmc = self.printer.lookup_object('%s manual_extruder_stepper gear_stepper' % chip, None)
                if self.gear_tmc is not None:
                    self._log_debug("Found %s on gear_stepper. Current control enabled. Stallguard 'touch' homing possible." % chip)
            if self.extruder_tmc is None:
                self.extruder_tmc = self.printer.lookup_object("%s manual_extruder_stepper %s" % (chip, self.extruder_name), None)
                if self.extruder_tmc is not None:
                    self._log_debug("Found %s on extruder. Current control enabled" % chip)

        if self.selector_tmc is None:
            self._log_debug("TMC driver not found for selector_stepper, cannot use sensorless homing and recovery")
        if self.extruder_tmc is None:
            self._log_debug("TMC driver not found for extruder, cannot use current increase for tip forming move")
        if self.gear_tmc is None:
            self._log_debug("TMC driver not found for gear_stepper, cannot use current reduction for collision detection or while synchronized printing")

        # Sanity check extruder name
        self.extruder = self.printer.lookup_object(self.extruder_name, None)
        if not self.extruder:
            raise self.config.error("Extruder named `%s` not found on printer" % self.extruder_name)

        # Sanity check required klipper options are enabled
        pause_resume = self.printer.lookup_object('pause_resume', None)
        if pause_resume is None:
            raise self.config.error("MMU requires [pause_resume] to work, please add it to your config!")

        if self.toolhead_sensor is None:
            self.home_to_extruder = 1
            self._log_debug("No toolhead sensor detected, setting 'home_to_extruder: 1'")

        if self.enable_endless_spool == 1 and self.enable_clog_detection == 0:
            self._log_info("Warning: EndlessSpool mode requires clog detection to be enabled")

        # Add the MCU defined (real) extruder stepper to the toolhead extruder and sync it to complete the setup
        self.extruder.extruder_stepper = self.mmu_extruder_stepper
        self.mmu_extruder_stepper.sync_to_extruder(self.extruder_name)

        self.variables = self.printer.lookup_object('save_variables').allVariables

        # Sanity check to see that mmu_vars.cfg is included (even if empty). This will verify path
        if self.variables == {}:
            raise self.config.error("Calibration settings not set. mmu_vars.cfg probably not found. Check [save_variables] section in mmu_software.cfg")

        # Remember user setting of idle_timeout so it can be restored (if not overridden)
        if self.timeout_unlock < 0:
            self.timeout_unlock = self.printer.lookup_object("idle_timeout").idle_timeout

        # Configure gear stepper calibration (set with MMU_CALIBRATE_GEAR)
        rotation_distance = self.variables.get(self.VARS_MMU_GEAR_ROTATION_DISTANCE, None)
        if rotation_distance:
            self.gear_stepper.stepper.set_rotation_distance(rotation_distance)
            self._log_debug("Loaded saved gear rotation distance: %.6f" % rotation_distance)
            self.calibration_status |= self.CALIBRATED_GEAR
        else:
            self._log_always("Warning: Gear rotation_distance not found in mmu_vars.cfg. Probably not calibrated")
        self.ref_gear_rotation_distance = self.gear_stepper.stepper.get_rotation_distance()[0]
 
        # Configure encoder calibration (set with MMU_CALIBRATE_ENCODER)
        self.encoder_sensor.set_logger(self._log_debug) # Combine with MMU log
        self.encoder_sensor.set_extruder(self.extruder_name)
        self.encoder_sensor.set_mode(self.enable_clog_detection)
        resolution = self.variables.get(self.VARS_MMU_ENCODER_RESOLUTION, None)
        if resolution:
            self.encoder_sensor.set_resolution(resolution)
            self._log_debug("Loaded saved encoder resolution: %.6f" % resolution)
            self.calibration_status |= self.CALIBRATED_ENCODER
        else:
            self._log_always("Warning: Encoder resolution not found in mmu_vars.cfg. Probably not calibrated")

        # Configure selector calibration (set with MMU_CALIBRATE_SELECTOR)
        selector_offsets = self.variables.get(self.VARS_MMU_SELECTOR_OFFSETS, None)
        if selector_offsets:
            if len(selector_offsets) == self.num_gates:
                self.selector_offsets = selector_offsets
                self._log_debug("Loaded saved selector offsets: %s" % selector_offsets)
                self.calibration_status |= self.CALIBRATED_SELECTOR
            else:
                self._log_error("Incorrect number of gates specified in %s" % self.VARS_MMU_SELECTOR_OFFSETS)
                self.selector_offsets = []
        else:
            self._log_always("Warning: Selector offsets not found in mmu_vars.cfg. Probably not calibrated")
            self.selector_offsets = []
        bypass_offset = self.variables.get(self.VARS_MMU_SELECTOR_BYPASS, None)
        if bypass_offset:
            self.bypass_offset = bypass_offset
            self._log_debug("Loaded saved bypass offset: %s" % bypass_offset)
        else:
            self.bypass_offset = 0

        # Set bowden length from calibration
        bowden_length = self.variables.get(self.VARS_MMU_CALIB_BOWDEN_LENGTH, None)
        if bowden_length:
            self.calibrated_bowden_length = bowden_length
            self._log_debug("Loaded saved reference bowden length: %.1f" % bowden_length)
            self.calibration_status |= self.CALIBRATED_BOWDEN
        else:
            self._log_always("Warning: Reference bowden length not found in mmu_vars.cfg. Probably not calibrated")

        # Restore state if fully calibrated
        if not self._check_is_calibrated(silent=True):
            self._load_persisted_state()

    def _initialize_state(self):
        self.is_enabled = True
        self.is_paused_locked = False
        self.is_homed = False
        self.paused_extruder_temp = 0.
        self.tool_selected = self._next_tool = self._last_tool = self.TOOL_UNKNOWN
        self._last_toolchange = "Unknown"
        self.gate_selected = self.GATE_UNKNOWN  # We keep record of gate selected in case user messes with mapping in print
        self.servo_state = self.servo_angle = self.SERVO_UNKNOWN_STATE
        self.loaded_status = self.LOADED_STATUS_UNKNOWN
        self.filament_direction = self.DIRECTION_LOAD
        self.action = self.ACTION_IDLE
        self.calibrating = False
        self.saved_toolhead_position = False
        self._reset_statistics()

    def _load_persisted_state(self):
        self._log_debug("Loaded persisted MMU state, level: %d" % self.persistence_level)
        errors = []

        if self.persistence_level >= 1:
            # Load EndlessSpool config
            self.enable_endless_spool = self.variables.get(self.VARS_MMU_ENABLE_ENDLESS_SPOOL, self.enable_endless_spool)
            endless_spool_groups = self.variables.get(self.VARS_MMU_ENDLESS_SPOOL_GROUPS, self.endless_spool_groups)
            if len(endless_spool_groups) == self.num_gates:
                self.endless_spool_groups = endless_spool_groups
            else:
                errors.append("Incorrect number of gates specified in %s" % self.VARS_MMU_ENDLESS_SPOOL_GROUPS)

        if self.persistence_level >= 2:
            # Load tool to gate map
            tool_to_gate_map = self.variables.get(self.VARS_MMU_TOOL_TO_GATE_MAP, self.tool_to_gate_map)
            if len(tool_to_gate_map) == self.num_gates:
                self.tool_to_gate_map = tool_to_gate_map
            else:
                errors.append("Incorrect number of gates specified in %s" % self.VARS_MMU_TOOL_TO_GATE_MAP)

        if self.persistence_level >= 3:
            # Load gate status (filament present or not)
            gate_status = self.variables.get(self.VARS_MMU_GATE_STATUS, self.gate_status)
            if len(gate_status) == self.num_gates:
                self.gate_status = gate_status
            else:
                errors.append("Incorrect number of gates specified in %s" % self.VARS_MMU_GATE_STATUS)

            # Load filament material at each gate
            gate_material = self.variables.get(self.VARS_MMU_GATE_MATERIAL, self.gate_material)
            if len(gate_status) == self.num_gates:
                self.gate_material = gate_material
            else:
                errors.append("Incorrect number of gates specified in %s" % self.VARS_MMU_GATE_MATERIAL)

            # Load filament color at each gate
            gate_color = self.variables.get(self.VARS_MMU_GATE_COLOR, self.gate_color)
            if len(gate_status) == self.num_gates:
                self.gate_color = gate_color
            else:
                errors.append("Incorrect number of gates specified in %s" % self.VARS_MMU_GATE_COLOR)

        if self.persistence_level >= 4:
            # Load selected tool and gate
            tool_selected = self.variables.get(self.VARS_MMU_TOOL_SELECTED, self.tool_selected)
            gate_selected = self.variables.get(self.VARS_MMU_GATE_SELECTED, self.gate_selected)
            if gate_selected < self.num_gates and tool_selected < self.num_gates:
                self.tool_selected = tool_selected
                self.gate_selected = gate_selected

                if self.gate_selected >= 0:
                    self._set_gate_ratio(self._get_gate_ratio(self.gate_selected))
                    offset = self.selector_offsets[self.gate_selected]
                    if self.tool_selected == self.TOOL_BYPASS: # Sanity check
                        self.tool_selected = self.TOOL_UNKNOWN
                    self.selector_stepper.do_set_position(offset)
                    self.is_homed = True
                elif self.gate_selected == self.TOOL_BYPASS:
                    self.tool_selected = self.TOOL_BYPASS # Sanity check
                    self.selector_stepper.do_set_position(self.bypass_offset)
                    self.is_homed = True
            else:
                errors.append("Incorrect number of gates specified in %s or %s" % (self.VARS_MMU_TOOL_SELECTED, self.VARS_MMU_GATE_SELECTED))
            if gate_selected != self.GATE_UNKNOWN and tool_selected != self.TOOL_UNKNOWN:
                self.loaded_status = self.variables.get(self.VARS_MMU_LOADED_STATUS, self.loaded_status)

        if len(errors) > 0:
            self._log_info("Warning: Some persisted state was ignored because it contained errors:\n%s" % ''.join(errors))

        swap_stats = self.variables.get(self.VARS_MMU_SWAP_STATISTICS, {})
        if swap_stats != {}:
            self.total_swaps = swap_stats['total_swaps']
            self.time_spent_loading = swap_stats['time_spent_loading']
            self.time_spent_unloading = swap_stats['time_spent_unloading']
            self.total_pauses = swap_stats['total_pauses']
            self.time_spent_paused = swap_stats['time_spent_paused']
        for gate in range(self.num_gates):
            self.gate_statistics[gate] = self.variables.get("%s%d" % (self.VARS_MMU_GATE_STATISTICS_PREFIX, gate), self.EMPTY_GATE_STATS_ENTRY.copy())

    def handle_disconnect(self):
        self._log_debug('MMU Shutdown')
        if self.queue_listener != None:
            self.queue_listener.stop()

    def handle_ready(self):
        self.printer.register_event_handler("idle_timeout:printing", self._handle_idle_timeout_printing)
        self.printer.register_event_handler("idle_timeout:ready", self._handle_idle_timeout_ready)
        self.printer.register_event_handler("idle_timeout:idle", self._handle_idle_timeout_idle)
        self._setup_heater_off_reactor()
        self.saved_toolhead_position = False

        # This is a bit naughty to register commands here but I need to make sure I'm the outermost wrapper
        try:
            prev_resume = self.gcode.register_command('RESUME', None)
            if prev_resume != None:
                self.gcode.register_command('__RESUME', prev_resume)
                self.gcode.register_command('RESUME', self.cmd_MMU_RESUME, desc = self.cmd_MMU_RESUME_help)
            else:
                self._log_always('No existing RESUME macro found!')

            prev_cancel = self.gcode.register_command('CANCEL_PRINT', None)
            if prev_cancel != None:
                self.gcode.register_command('__CANCEL_PRINT', prev_cancel)
                self.gcode.register_command('CANCEL_PRINT', self.cmd_MMU_CANCEL_PRINT, desc = self.cmd_MMU_CANCEL_PRINT_help)
            else:
                self._log_always('No existing CANCEL_PRINT macro found!')
        except Exception as e:
            self._log_always('Warning: Error trying to wrap RESUME macro: %s' % str(e))

        self.estimated_print_time = self.printer.lookup_object('mcu').estimated_print_time
        self.last_selector_move_time = self.estimated_print_time(self.reactor.monotonic())
        waketime = self.reactor.monotonic() + self.BOOT_DELAY
        self.reactor.register_callback(self._bootup_tasks, waketime)

    def _bootup_tasks(self, eventtime):
        try:
            self.encoder_sensor.set_clog_detection_length(self.variables.get(self.VARS_MMU_CALIB_CLOG_LENGTH, 15))
            self._log_always('(\_/)\n( *,*)\n(")_(") MMU Ready')
            if self.startup_status > 0:
                self._log_always(self._tool_to_gate_map_to_human_string(self.startup_status == 1))
                self._display_visual_state(silent=(self.persistence_level < 4))
            self._servo_auto()
        except Exception as e:
            self._log_always('Warning: Error booting up MMU: %s' % str(e))

####################################
# LOGGING AND STATISTICS FUNCTIONS #
####################################

    def _get_action_string(self):
        return ("Idle" if self.action == self.ACTION_IDLE else
                "Loading" if self.action == self.ACTION_LOADING else
                "Unloading" if self.action == self.ACTION_UNLOADING else
                "Loading Ext" if self.action == self.ACTION_LOADING_EXTRUDER else
                "Exiting Ext" if self.action == self.ACTION_UNLOADING_EXTRUDER else
                "Forming Tip" if self.action == self.ACTION_FORMING_TIP else
                "Heating" if self.action == self.ACTION_HEATING else
                "Checking" if self.action == self.ACTION_CHECKING else
                "Homing" if self.action == self.ACTION_HOMING else
                "Selecting" if self.action == self.ACTION_SELECTING else
                "Unknown") # Error case - should not happen

    def get_status(self, eventtime):
        return {
                'enabled': self.is_enabled,
                'is_locked': self.is_paused_locked,
                'is_homed': self.is_homed,
                'tool': self.tool_selected,
                'gate': self.gate_selected,
                'material': self.gate_material[self.gate_selected] if self.gate_selected >= 0 else '',
                'next_tool': self._next_tool,
                'last_tool': self._last_tool,
                'last_toolchange': self._last_toolchange,
                'clog_detection': self.enable_clog_detection,
                'endless_spool': self.enable_endless_spool,
                'filament': "Loaded" if self.loaded_status == self.LOADED_STATUS_FULL else
                            "Unloaded" if self.loaded_status == self.LOADED_STATUS_UNLOADED else
                            "Unknown",
                'loaded_status': self.loaded_status,
                'filament_direction': self.filament_direction,
                'servo': "Up" if self.servo_state == self.SERVO_UP_STATE else
                         "Down" if self.servo_state == self.SERVO_DOWN_STATE else
                         "Move" if self.servo_state == self.SERVO_MOVE_STATE else
                         "Unknown",
                'ttg_map': list(self.tool_to_gate_map),
                'gate_status': list(self.gate_status),
                'gate_material': list(self.gate_material),
                'gate_color': list(self.gate_color),
                'endless_spool_groups': list(self.endless_spool_groups),
                'action': self._get_action_string()
        }

    def _reset_statistics(self):
        self.total_swaps = 0
        self.time_spent_loading = 0
        self.time_spent_unloading = 0
        self.total_pauses = 0
        self.time_spent_paused = 0
        self.tracked_start_time = 0
        self.pause_start_time = 0

        self.gate_statistics = []
        for gate in range(self.num_gates):
            self.gate_statistics.append(self.EMPTY_GATE_STATS_ENTRY.copy())

    def _track_swap_completed(self):
        self.total_swaps += 1

    def _track_load_start(self):
        self.tracked_start_time = time.time()

    def _track_load_end(self):
        self.time_spent_loading += time.time() - self.tracked_start_time

    def _track_unload_start(self):
        self.tracked_start_time = time.time()

    def _track_unload_end(self):
        self.time_spent_unloading += time.time() - self.tracked_start_time

    def _track_pause_start(self):
        self.total_pauses += 1
        self.pause_start_time = time.time()
        self._track_gate_statistics('pauses', self.gate_selected)

    def _track_pause_end(self):
        self.time_spent_paused += time.time() - self.pause_start_time

    # Per gate tracking
    def _track_gate_statistics(self, key, gate, count=1):
        try:
            if gate >= self.GATE_UNKNOWN:
                if isinstance(count, float):
                    self.gate_statistics[gate][key] = round(self.gate_statistics[gate][key] + count, 3)
                else:
                    self.gate_statistics[gate][key] += count
            else:
                self._log_debug("Unknown gate provided to record gate stats")
        except Exception as e:
            self._log_debug("Exception whilst tracking gate stats: %s" % str(e))

    def _seconds_to_human_string(self, seconds):
        result = ""
        hours = int(math.floor(seconds / 3600.))
        if hours >= 1:
            result += "%d hours " % hours
        minutes = int(math.floor(seconds / 60.) % 60)
        if hours >= 1 or minutes >= 1:
            result += "%d minutes " % minutes
        result += "%d seconds" % int((math.floor(seconds) % 60))
        return result

    def _swap_statistics_to_human_string(self):
        msg = "MMU Statistics:"
        msg += "\n%d swaps completed" % self.total_swaps
        msg += "\n%s spent loading (average: %s)" % (self._seconds_to_human_string(self.time_spent_loading),
                                                    self._seconds_to_human_string(self.time_spent_loading / self.total_swaps) if self.total_swaps > 0 else "0")
        msg += "\n%s spent unloading (average: %s)" % (self._seconds_to_human_string(self.time_spent_unloading),
                                                      self._seconds_to_human_string(self.time_spent_unloading / self.total_swaps) if self.total_swaps > 0 else "0")
        msg += "\n%s spent paused (total pauses: %d)" % (self._seconds_to_human_string(self.time_spent_paused), self.total_pauses)
        return msg

    def _dump_statistics(self, report=False, quiet=False):
        if (self.log_statistics or report) and not quiet:
            self._log_always(self._swap_statistics_to_human_string())
            self._dump_gate_statistics()
        # This is good place to update the persisted stats...
        self._persist_swap_statistics()
        self._persist_gate_statistics()

    def _dump_gate_statistics(self):
        msg = "Gate Statistics:\n"
        dbg = ""
        for gate in range(self.num_gates):
            #rounded = {k:round(v,1) if isinstance(v,float) else v for k,v in self.gate_statistics[gate].items()}
            rounded = self.gate_statistics[gate]
            load_slip_percent = (rounded['load_delta'] / rounded['load_distance']) * 100 if rounded['load_distance'] != 0. else 0.
            unload_slip_percent = (rounded['unload_delta'] / rounded['unload_distance']) * 100 if rounded['unload_distance'] != 0. else 0.
            # Give the gate a reliability grading based on slippage
            grade = load_slip_percent + unload_slip_percent
            if rounded['load_distance'] + rounded['unload_distance'] == 0.:
                status = "n/a"
            elif grade < 2.:
                status = "Good"
            elif grade < 4.:
                status = "Marginal"
            elif grade < 6.:
                status = "Degraded"
            elif grade < 10.:
                status = "Poor"
            else:
                status = "Terrible"
            msg += "#%d: %s" % (gate, status)
            msg += ", " if gate < (self.num_gates - 1) else ""
            dbg += "\nGate #%d: " % gate
            dbg += "Load: (monitored: %.1fmm slippage: %.1f%%)" % (rounded['load_distance'], load_slip_percent)
            dbg += "; Unload: (monitored: %.1fmm slippage: %.1f%%)" % (rounded['unload_distance'], unload_slip_percent)
            dbg += "; Failures: (servo: %d load: %d unload: %d pauses: %d)" % (rounded['servo_retries'], rounded['load_failures'], rounded['unload_failures'], rounded['pauses'])
        self._log_always(msg)
        self._log_debug(dbg)

    def _persist_gate_statistics(self):
        for gate in range(self.num_gates):
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s%d VALUE=\"%s\"" % (self.VARS_MMU_GATE_STATISTICS_PREFIX, gate, self.gate_statistics[gate]))
        # Good place to persist current clog length
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%.1f" % (self.VARS_MMU_CALIB_CLOG_LENGTH, self.encoder_sensor.get_clog_detection_length()))

    def _persist_swap_statistics(self):
        swap_stats = {
            'total_swaps': self.total_swaps,
            'time_spent_loading': round(self.time_spent_loading, 1),
            'time_spent_unloading': round(self.time_spent_unloading, 1),
            'total_pauses': self.total_pauses,
            'time_spent_paused': self.time_spent_paused
            }
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=\"%s\"" % (self.VARS_MMU_SWAP_STATISTICS, swap_stats))

    def _persist_gate_map(self):
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_GATE_STATUS, self.gate_status))
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_GATE_MATERIAL, list(map(lambda x: ("\'%s\'" %x), self.gate_material))))
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_GATE_COLOR, list(map(lambda x: ("\'%s\'" %x), self.gate_color))))

    def _log_error(self, message):
        if self.mmu_logger:
            self.mmu_logger.info(message)
        self.gcode.respond_raw("!! %s" % message)

    def _log_always(self, message):
        if self.mmu_logger:
            self.mmu_logger.info(message)
        self.gcode.respond_info(message)

    def _log_info(self, message):
        if self.mmu_logger and self.logfile_level > 0:
            self.mmu_logger.info(message)
        if self.log_level > 0:
            self.gcode.respond_info(message)

    def _log_debug(self, message):
        message = "- DEBUG: %s" % message
        if self.mmu_logger and self.logfile_level > 1:
            self.mmu_logger.info(message)
        if self.log_level > 1:
            self.gcode.respond_info(message)

    def _log_trace(self, message):
        message = "- - TRACE: %s" % message
        if self.mmu_logger and self.logfile_level > 2:
            self.mmu_logger.info(message)
        if self.log_level > 2:
            self.gcode.respond_info(message)

    def _log_stepper(self, message):
        message = "- - - STEPPER: %s" % message
        if self.mmu_logger and self.logfile_level > 3:
            self.mmu_logger.info(message)
        if self.log_level > 3:
            self.gcode.respond_info(message)

    # Fun visual display of MMU state
    def _display_visual_state(self, direction=None, silent=False):
        if not direction == None:
            self.filament_direction = direction
        if not silent and self.log_visual > 0 and not self.calibrating:
            visual_str = self._state_to_human_string()
            self._log_always(visual_str)

    def _state_to_human_string(self, direction=None):
        tool_str = str(self.tool_selected) if self.tool_selected >=0 else "?"
        sensor_str = " [sensor] " if self._has_toolhead_sensor() else ""
        counter_str = " (@%.1f mm)" % self.encoder_sensor.get_distance()
        visual = visual2 = ""
        if self.tool_selected == self.TOOL_BYPASS and self.loaded_status == self.LOADED_STATUS_FULL:
            visual = "MMU BYPASS ----- [encoder] ----------->> [nozzle] LOADED"
        elif self.tool_selected == self.TOOL_BYPASS and self.loaded_status == self.LOADED_STATUS_UNLOADED:
            visual = "MMU BYPASS >.... [encoder] ............. [nozzle] UNLOADED"
        elif self.tool_selected == self.TOOL_BYPASS:
            visual = "MMU BYPASS >.... [encoder] ............. [nozzle] UNKNOWN"
        elif self.loaded_status == self.LOADED_STATUS_UNKNOWN:
            visual = "MMU [T%s] ..... [encoder] ............. [extruder] ...%s... [nozzle] UNKNOWN" % (tool_str, sensor_str)
        elif self.loaded_status == self.LOADED_STATUS_UNLOADED:
            visual = "MMU [T%s] >.... [encoder] ............. [extruder] ...%s... [nozzle] UNLOADED" % (tool_str, sensor_str)
            visual += counter_str
        elif self.loaded_status == self.LOADED_STATUS_PARTIAL_BEFORE_ENCODER:
            visual = "MMU [T%s] >>>.. [encoder] ............. [extruder] ...%s... [nozzle]" % (tool_str, sensor_str)
            visual += counter_str
        elif self.loaded_status == self.LOADED_STATUS_PARTIAL_PAST_ENCODER:
            visual = "MMU [T%s] >>>>> [encoder] >>........... [extruder] ...%s... [nozzle]" % (tool_str, sensor_str)
            visual += counter_str
        elif self.loaded_status == self.LOADED_STATUS_PARTIAL_IN_BOWDEN:
            visual = "MMU [T%s] >>>>> [encoder] >>>>>>>...... [extruder] ...%s... [nozzle]" % (tool_str, sensor_str)
            visual += counter_str
        elif self.loaded_status == self.LOADED_STATUS_PARTIAL_END_OF_BOWDEN:
            visual = "MMU [T%s] >>>>> [encoder] >>>>>>>>>>>>> [extruder] ...%s... [nozzle]" % (tool_str, sensor_str)
            visual += counter_str
        elif self.loaded_status == self.LOADED_STATUS_PARTIAL_HOMED_EXTRUDER:
            visual = "MMU [T%s] >>>>> [encoder] >>>>>>>>>>>>| [extruder] ...%s... [nozzle]" % (tool_str, sensor_str)
            visual += counter_str
        elif self.loaded_status == self.LOADED_STATUS_PARTIAL_HOMED_SENSOR:
            visual = "MMU [T%s] >>>>> [encoder] >>>>>>>>>>>>> [extruder] >>|%s... [nozzle]" % (tool_str, sensor_str)
            visual += counter_str
        elif self.loaded_status == self.LOADED_STATUS_PARTIAL_IN_EXTRUDER:
            visual = "MMU [T%s] >>>>> [encoder] >>>>>>>>>>>>> [extruder] >>>%s>.. [nozzle]" % (tool_str, sensor_str)
            visual += counter_str
        elif self.loaded_status == self.LOADED_STATUS_FULL:
            visual = "MMU [T%s] >>>>> [encoder] >>>>>>>>>>>>> [extruder] >>>%s>>> [nozzle] LOADED" % (tool_str, sensor_str)
            visual += counter_str

        visual2 = visual.replace("encoder", "En").replace("extruder", "Ex").replace("sensor", "Ts").replace("nozzle", "Nz").replace(">>", ">").replace("..", ".").replace("--", "-")
        if self.filament_direction == self.DIRECTION_UNLOAD:
            visual = visual.replace(">", "<")
            visual2 = visual2.replace(">", "<")
        return visual2 if self.log_visual == 2 else visual

    def _log_level_to_human_string(self, level):
        log = "OFF"
        if level > 3: log = "STEPPER"
        elif level > 2: log = "TRACE"
        elif level > 1: log = "DEBUG"
        elif level > 0: log = "INFO"
        elif level > -1: log = "ESSENTIAL MESSAGES"
        return log

    def _visual_log_level_to_human_string(self, level):
        log = "OFF"
        if level > 1: log = "SHORT"
        elif level > 0: log = "LONG"
        return log

### LOGGING AND STATISTICS FUNCTIONS GCODE FUNCTIONS

    cmd_MMU_STATS_help = "Dump and optionally reset the MMU statistics"
    def cmd_MMU_STATS(self, gcmd):
        if self._check_is_disabled(): return
        reset = gcmd.get_int('RESET', 0, minval=0, maxval=1)
        if reset:
            self._reset_statistics()
            self._persist_swap_statistics()
            self._persist_gate_statistics()
        self._dump_statistics(report=True)

    cmd_MMU_STATUS_help = "Complete dump of current MMU state and important configuration"
    def cmd_MMU_STATUS(self, gcmd):
        config = gcmd.get_int('SHOWCONFIG', 0, minval=0, maxval=1)
        detail = gcmd.get_int('DETAIL', 0, minval=0, maxval=1)

        msg = "%s v%s" % (self.mmu_vendor, self.mmu_version_string)
        msg += " with %d gates" % (self.num_gates)
        msg += " is %s" % ("DISABLED" if not self.is_enabled else "PAUSED/LOCKED" if self.is_paused_locked else "OPERATIONAL")
        msg += " with the servo in a %s position" % ("UP" if self.servo_state == self.SERVO_UP_STATE else \
                "DOWN" if self.servo_state == self.SERVO_DOWN_STATE else "MOVE" if self.servo_state == self.SERVO_MOVE_STATE else "unknown")
        msg += ", Encoder reads %.2fmm" % self.encoder_sensor.get_distance()
        msg += "\nSelector is %shomed" % ("" if self.is_homed else "NOT ")
        msg += ". Tool %s is selected " % self._selected_tool_string()
        msg += " on gate %s" % self._selected_gate_string()
        msg += ". Toolhead position saved pending resume" if self.saved_toolhead_position else ""

        if detail:
            msg += "\nFilament position:%s" % self._state_to_human_string()

        if config:
            msg += "\n\nConfiguration:\nFilament homes"
            if self._must_home_to_extruder():
                if self.extruder_homing_endstop == self.EXTRUDER_COLLISION_ENDSTOP:
                    msg += " to EXTRUDER using COLLISION DETECTION (current %d%%)" % self.extruder_homing_current
                else:
                    msg += " to EXTRUDER to endstop '%s'" % self.extruder_homing_endstop
                if self._has_toolhead_sensor():
                    msg += " and then"
            msg += " to TOOLHEAD SENSOR" if self._has_toolhead_sensor() else ""
            msg += " after an initial %.1fmm fast move" % self.calibrated_bowden_length
            if self.sync_load_extruder or self.sync_unload_extruder or self.sync_form_tip or self.sync_to_extruder:
                msg += "\nGear and Extruder steppers are synchronized during: "
                msg += "extruder load, " if self.sync_load_extruder else ""
                msg += "extruder unload, " if self.sync_unload_extruder else ""
                msg += "tip forming, " if self.sync_form_tip else ""
                msg += ("print (at %d%% current)" % self.sync_gear_current) if self.sync_to_extruder else ""
            msg += "\nTip forming extruder current is %d%%" % self.extruder_form_tip_current
            msg += "\nSelector %s touch (stallguard) capability enabled - blocked gate detection and recovery %s possible" % (("has", "may be") if self.selector_touch else ("does not have", "is not"))
            msg += "\nClog detection is %s" % ("AUTOMATIC" if self.enable_clog_detection == self.encoder_sensor.RUNOUT_AUTOMATIC else "ENABLED" if self.enable_clog_detection == self.encoder_sensor.RUNOUT_STATIC else "DISABLED")
            msg += " (%.1fmm runout)" % self.encoder_sensor.get_clog_detection_length()
            msg += " and EndlessSpool is %s" % ("ENABLED" if self.enable_endless_spool else "DISABLED")
            p = self.persistence_level
            msg += "\n%s state is persisted across restarts" % ("All" if p == 4 else "Gate status & TTG map & EndlessSpool groups" if p == 3 else "TTG map & EndlessSpool groups" if p == 2 else "EndlessSpool groups" if p == 1 else "No")
            msg += "\nLogging levels: Console %d(%s)" % (self.log_level, self._log_level_to_human_string(self.log_level))
            msg += ", Logfile %d(%s)" % (self.logfile_level, self._log_level_to_human_string(self.logfile_level))
            msg += ", Visual %d(%s)" % (self.log_visual, self._visual_log_level_to_human_string(self.log_visual))
            msg += ", Statistics %d(%s)" % (self.log_statistics, "ON" if self.log_statistics else "OFF")

        if detail:
            msg += "\n\nTool/gate mapping%s" % (" and EndlessSpool groups:" if self.enable_endless_spool else ":")
            msg += "\n%s" % self._tool_to_gate_map_to_human_string()
            msg += "\n\n%s" % self._swap_statistics_to_human_string()
        else:
            msg += "\nFor details and endless spool groups use 'MMU_STATUS DETAIL=1'"
            msg += "\n\n%s" % self._tool_to_gate_map_to_human_string(summary=True)
            msg += "\n\n%s" % self._state_to_human_string()
        self._log_always(msg)


#############################
# SERVO AND MOTOR FUNCTIONS #
#############################

    def _servo_set_angle(self, angle):
        self.servo.set_value(angle=angle, duration=self.servo_duration)
        self.servo_state = self.SERVO_UNKNOWN_STATE

    def _servo_down(self, buzz_gear=True):
        if self.gate_selected == self.TOOL_BYPASS: return
        if self.servo_state == self.SERVO_DOWN_STATE: return
        self._log_debug("Setting servo to down (filament drive) position at angle: %d" % self.servo_down_angle)
        if not self.servo_angle == self.servo_down_angle:
            self.toolhead.wait_moves()
            self.servo.set_value(angle=self.servo_down_angle, duration=self.servo_duration)
            if buzz_gear:
                oscillations = 2
                for i in range(oscillations):
                    self.toolhead.dwell(0.05)
                    self._gear_stepper_move_wait(0.5, speed=25, accel=self.gear_buzz_accel, wait=False, sync=False)
                    self.toolhead.dwell(0.05)
                    self._gear_stepper_move_wait(-0.5, speed=25, accel=self.gear_buzz_accel, wait=False, sync=(i == oscillations - 1))
                self.toolhead.dwell(max(0., self.servo_duration - (0.1 * oscillations)))
            self.servo_angle = self.servo_down_angle
        self.servo_state = self.SERVO_DOWN_STATE

    def _servo_move(self): # Position servo for selector movement
        if self.servo_state == self.SERVO_MOVE_STATE: return
        self._log_debug("Setting servo to move (filament hold) position at angle: %d" % self.servo_move_angle)
        if not self.servo_angle == self.servo_move_angle:
            self.toolhead.wait_moves()
            self.servo.set_value(angle=self.servo_move_angle, duration=self.servo_duration)
            self.toolhead.dwell(min(self.servo_duration, 0.4))
            self.toolhead.wait_moves()
            self.servo_angle = self.servo_move_angle
        self.servo_state = self.SERVO_MOVE_STATE

    def _servo_up(self):
        if self.tool_selected < 0:
            self._log_debug("PAUL TEMP: ****** Assertion failure - servo_up() called but no tool loaded")
        if self.servo_state == self.SERVO_UP_STATE: return 0.
        self._log_debug("Setting servo to up (filament released) position at angle: %d" % self.servo_up_angle)
        delta = 0.
        if not self.servo_angle == self.servo_up_angle:
            self.toolhead.wait_moves()
            initial_encoder_position = self.encoder_sensor.get_distance()
            self.servo.set_value(angle=self.servo_up_angle, duration=self.servo_duration)
            self.servo_angle = self.servo_up_angle
            # Report on spring back in filament then reset counter
            self.toolhead.dwell(min(self.servo_duration, 0.4))
            self.toolhead.wait_moves()
            delta = self.encoder_sensor.get_distance() - initial_encoder_position
            if delta > 0.:
                self._log_debug("Spring in filament measured  %.1fmm - adjusting encoder" % delta)
                self.encoder_sensor.set_distance(initial_encoder_position)
        self.servo_state = self.SERVO_UP_STATE
        return delta

    def _servo_auto(self):
        if not self.is_homed or self.tool_selected < 0 or self.gate_selected < 0:
            self._servo_move()
        else:
            self._servo_up()

    def _motors_off(self, motor="all"):
        if motor == "all" or motor == "gear":
            self._sync_gear_to_extruder(False)
            self.gear_stepper.do_enable(False)
        if motor == "all" or motor == "selector":
            self.selector_stepper.do_enable(False)
            self.is_homed = False
            self._set_tool_selected(self.TOOL_UNKNOWN)

### SERVO AND MOTOR GCODE FUNCTIONS

    cmd_MMU_SERVO_help = "Move MMU servo to position specified position or angle"
    def cmd_MMU_SERVO(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        pos = gcmd.get('POS', "").lower()
        if pos == "up":
            self._servo_up()
        elif pos == "move":
            self._servo_move()
        elif pos == "down":
            if self._check_in_bypass(): return
            self._servo_down()
        elif pos == "":
            if self._check_in_bypass(): return
            angle = gcmd.get_float('ANGLE', None)
            if angle is not None:
                self._log_debug("Setting servo to angle: %d" % angle)
                self._servo_set_angle(angle)
            else:
                self._log_error("No position or angle specified. Try POS=... or ANGLE=...")
        else:
            self._log_error("Unknown servo position `%s`" % pos)

    cmd_MMU_MOTORS_OFF_help = "Turn off both MMU motors"
    def cmd_MMU_MOTORS_OFF(self, gcmd):
        if self._check_is_disabled(): return
        self._motors_off()
        self._servo_auto()

    cmd_MMU_TEST_BUZZ_MOTOR_help = "Simple buzz the selected motor (default gear) for setup testing"
    def cmd_MMU_TEST_BUZZ_MOTOR(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_in_bypass(): return
        motor = gcmd.get('MOTOR', "gear")
        if motor == "gear":
            found = self._buzz_gear_motor()
            self._log_info("Filament %s by gear motor buzz" % ("detected" if found else "not detected"))
        elif motor == "selector":
            pos = self.selector_stepper.stepper.get_commanded_position()
            self._selector_stepper_move_wait(pos + 5, wait=False)
            self._selector_stepper_move_wait(pos - 5, wait=False)
            self._selector_stepper_move_wait(pos + 5, wait=False)
            self._selector_stepper_move_wait(pos - 5, wait=False)
        elif motor == "servo":
            self.toolhead.wait_moves()
            old_state = self.servo_state
            small=min(self.servo_down_angle, self.servo_up_angle)
            large=max(self.servo_down_angle, self.servo_up_angle)
            mid=(self.servo_down_angle + self.servo_up_angle)/2
            self.servo.set_value(angle=mid, duration=self.servo_duration)
            self.toolhead.dwell(min(self.servo_duration, 0.5))
            self.servo.set_value(angle=abs(mid+small)/2, duration=self.servo_duration)
            self.toolhead.dwell(min(self.servo_duration, 0.5))
            self.servo.set_value(angle=abs(mid+large)/2, duration=self.servo_duration)
            self.toolhead.dwell(min(self.servo_duration, 0.5))
            self.toolhead.wait_moves()
            if old_state == self.SERVO_DOWN_STATE:
                self._servo_down(buzz_gear=False)
            elif old_state == self.SERVO_MOVE_STATE:
                self._servo_move()
            else:
                self._servo_up()
        else:
            raise gcmd.error("Motor '%s' not known" % motor)

    cmd_MMU_SYNC_GEAR_MOTOR_help = "Sync the MMU gear motor to the extruder motor"
    def cmd_MMU_SYNC_GEAR_MOTOR(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_in_bypass(): return
        servo = gcmd.get_int('SERVO', 1, minval=0, maxval=1)
        sync = gcmd.get_int('SYNC', 1, minval=0, maxval=1)
        self._sync_gear_to_extruder(sync, servo)


#########################
# CALIBRATION FUNCTIONS #
#########################

    def _set_calibrated_bowden_length(self, reference):
        self.variables[self.VARS_MMU_CALIB_BOWDEN_LENGTH] = reference
        self.calibrated_bowden_length = reference
        self.calibration_status |= self.CALIBRATED_BOWDEN

    def _get_gate_ratio(self, gate):
        if gate < 0: return 1.
        ratio = self.variables.get("%s%d" % (self.VARS_MMU_CALIB_PREFIX, gate), 1.)
        if ratio > 0.8 and ratio < 1.2:
            return ratio
        else:
            self._log_always("Warning: %s%d value (%.6f) is invalid. Using reference 1.0. Re-run MMU_CALIBRATE_GATES GATE=%d" % (self.VARS_MMU_CALIB_PREFIX, gate, ratio, gate))
            return 1.

    def _calibrate_encoder(self, length, repeats, speed, min_speed, max_speed, accel, save=True):
        try:
            pos_values, neg_values = [], []
            self._log_always("Testing over %.1fmm" % length)
            speed_incr = (max_speed - min_speed) / repeats
            test_speed = min_speed
            for x in range(repeats):
                if (speed_incr > 0.):
                    self._log_always("Test run #%d, Speed=%.1f mm/s" % (x, test_speed))
                # Move forward
                self.encoder_sensor.reset_counts()    # Encoder 0000
                self._gear_stepper_move_wait(length, wait=True, speed=test_speed, accel=accel)
                counts = self.encoder_sensor.get_counts()
                pos_values.append(counts)
                self._log_always("+ counts =  %d" % counts)
                # Move backward
                self.encoder_sensor.reset_counts()    # Encoder 0000
                self._gear_stepper_move_wait(-length, wait=True, speed=test_speed, accel=accel)
                counts = self.encoder_sensor.get_counts()
                neg_values.append(counts)
                self._log_always("- counts =  %d" % counts)
                if counts == 0: break
                test_speed += speed_incr
    
            self._log_always("Load direction: mean=%(mean).2f stdev=%(stdev).2f"
                              " min=%(min)d max=%(max)d range=%(range)d"
                              % self._sample_stats(pos_values))
            self._log_always("Unload direction: mean=%(mean).2f stdev=%(stdev).2f"
                              " min=%(min)d max=%(max)d range=%(range)d"
                              % self._sample_stats(neg_values))
    
            mean_pos = self._sample_stats(pos_values)['mean']
            mean_neg = self._sample_stats(neg_values)['mean']
            mean = (float(mean_pos) + float(mean_neg)) / 2
    
            if mean == 0:
                self._log_always("No counts measured. Ensure a tool was selected with servo down " +
                                  "before running calibration and that your encoder " +
                                  "is working properly")
                return
    
            resolution = length / mean
            old_result = mean * self.encoder_sensor.get_resolution()
            new_result = mean * resolution
    
            # Sanity check to ensure all teeth are reflecting / being counted. 20% tolerance
            if (abs(resolution - self.encoder_min_resolution) / self.encoder_min_resolution) > 0.2:
                self._log_always("Warning: Encoder is not detecting the expected number of counts. It is possible that reflections from some teeth are unreliable")
    
            msg = "Before calibration measured length = %.2f" % old_result
            msg += "\nResulting resolution of the encoder = %.6f" % resolution
            msg += "\nAfter calibration measured length = %.2f" % new_result
            self._log_always(msg)
    
            if save:
                self.encoder_sensor.set_resolution(resolution)
                self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%.6f" % (self.VARS_MMU_ENCODER_RESOLUTION, resolution))
                self._log_always("Encoder calibration has been saved for MMU %s v%s" % (self.mmu_vendor, self.mmu_version_string))
                self.calibration_status |= self.CALIBRATED_ENCODER

        except MmuError as ee:
            # Add some more context to the error and re-raise
            raise MmuError("Calibration of encoder failed. Aborting, because:\n%s" % str(ee))
        finally:
            if mean == 0:
                self._set_loaded_status(self.LOADED_STATUS_UNKNOWN)
            else:
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_BOWDEN)

    def _calibrate_bowden_length(self, start_pos, extruder_homing_max, repeats, save=True):
        try:
            self._log_always("Calibrating bowden length from reference Gate #0")
            self._select_tool(0)
            self._set_gate_ratio(1.)
            reference_sum = spring_max = 0.
            successes = 0
            for i in range(repeats):
                self.encoder_sensor.reset_counts()    # Encoder 0000
                encoder_moved = self._load_encoder(retry=False)
                self._load_bowden(start_pos - encoder_moved)
                self._log_info("Finding extruder gear position (try #%d of %d)..." % (i+1, repeats))
                self._home_to_extruder(extruder_homing_max)
                measured_movement = self.encoder_sensor.get_distance()
                spring = self._servo_up()
                reference = measured_movement - (spring * 0.5)
                if spring > 0:
                    if self._must_home_to_extruder():
                        # Home to extruder step is enabled so we don't need any spring
                        # in filament since we will do it again on every load
                        reference = measured_movement - (spring * 1.0)
                    elif self.delay_servo_release > 0:
                        # Keep all the spring pressure
                        reference = measured_movement

                    msg = "Pass #%d: Filament homed to extruder, encoder measured %.1fmm, " % (i+1, measured_movement)
                    msg += "filament sprung back %.1fmm" % spring
                    msg += "\n- Bowden calibration based on this pass is %.1f" % reference
                    self._log_always(msg)
                    reference_sum += reference
                    spring_max = max(spring, spring_max)
                    successes += 1
                else:
                    # No spring means we haven't reliably homed
                    self._log_always("Failed to detect a reliable home position on this attempt")

                self.encoder_sensor.reset_counts()    # Encoder 0000
                self._unload_bowden(reference - self.unload_buffer)
                self._unload_encoder(self.unload_buffer)
                self._set_loaded_status(self.LOADED_STATUS_UNLOADED)

            if successes > 0:
                average_reference = reference_sum / successes
                detection_length = (average_reference * 2) / 100 + spring_max # 2% of bowden length plus spring seems to be good starting point
                msg = "Recommended calibration reference is %.1fmm" % average_reference
                if self.enable_clog_detection:
                    msg += ". Clog detection length: %.1fmm" % detection_length
                self._log_always(msg)

                self._set_calibrated_bowden_length(average_reference)
                if save:
                    self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%.1f" % (self.VARS_MMU_CALIB_BOWDEN_LENGTH, average_reference))
                    self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s%d VALUE=1.0" % (self.VARS_MMU_CALIB_PREFIX, 0))
                    self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%.1f" % (self.VARS_MMU_CALIB_CLOG_LENGTH, detection_length))
                    self.encoder_sensor.set_clog_detection_length(detection_length)
                    self._log_always("Bowden calibration and clog detection length have been saved for MMU %s v%s" % (self.mmu_vendor, self.mmu_version_string))
            else:
                self._log_always("All %d attempts at homing failed. MMU needs some adjustments!" % repeats)
        except MmuError as ee:
            # Add some more context to the error and re-raise
            raise MmuError("Calibration of bowden length (on Gate #0) failed. Aborting, because:\n%s" % str(ee))
        finally:
            self._servo_auto()

    def _calibrate_gate(self, gate, calibration_length=None, save=True):
        try:
            if not calibration_length:
                calibration_length = max(self.calibrated_bowden_length - 100, 200)
            self._select_tool(gate)
            self._set_gate_ratio(1.)
            encoder_moved = self._load_encoder(retry=False)
            test_length = round((calibration_length - encoder_moved) / 50) * 50
            self.encoder_sensor.reset_counts()    # Encoder 0000
            self._log_always("%s gate %d..." % ("Calibrating" if gate > 0 else "Validating calibration of", gate))
            delta = self._trace_filament_move("Calibration load movement", test_length)
            delta = self._trace_filament_move("Calibration unload movement", -test_length)
            measurement = self.encoder_sensor.get_distance()
            ratio = (test_length * 2) / measurement
            self._log_always("Calibration move of 2x %.1fmm, average encoder measurement: %.1fmm - Ratio is %.6f" % (test_length, measurement / 2, ratio))
            if not gate == 0: # Gate #0 is not calibrated, it is the reference
                if ratio > 0.8 and ratio < 1.2:
                    if save:
                        self.variables["%s%d" % (self.VARS_MMU_CALIB_PREFIX, gate)] = ratio
                        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s%d VALUE=%.6f" % (self.VARS_MMU_CALIB_PREFIX, gate, ratio))
                        self._log_always("Calibration for gate #%d has been saved for MMU %s v%s" % (gate, self.mmu_vendor, self.mmu_version_string))
                        self.calibration_status |= self.CALIBRATED_GATES
                else:
                    self._log_always("Calibration ratio ignored because it is not considered valid (0.8 < ratio < 1.2)")
            self._unload_encoder(self.unload_buffer)
            self._set_loaded_status(self.LOADED_STATUS_UNLOADED)
        except MmuError as ee:
            # Add some more context to the error and re-raise
            raise MmuError("Calibration for gate #%d failed. Aborting, because: %s" % (gate, str(ee)))
        finally:
            self._servo_auto()

    def _measure_to_home(self, max_movement):
        selector_steps = self.selector_stepper.stepper.get_step_dist()
        init_mcu_pos = self.selector_stepper.stepper.get_mcu_position()
        self.selector_stepper.do_set_position(0.)
        found_home = False
        try:
            self._selector_stepper_move_wait(-max_movement, speed=self.selector_homing_speed, homing_move=1)
            self.selector_stepper.do_set_position(0.)
            self._selector_stepper_move_wait(5, False)                    # Ensure some bump space
            self._selector_stepper_move_wait(-5, speed=10, homing_move=1) # Slower more accurate homing move
            found_home = True
        except Exception as e:
            pass # Home definitely not found
        mcu_position = self.selector_stepper.stepper.get_mcu_position()
        traveled = abs(mcu_position - init_mcu_pos) * selector_steps
        return traveled, found_home

    def _get_max_movement(self, gate):
        n = gate if gate >= 0 else (self.num_gates - 1)
        if self.mmu_version >= 2.0:
            max_movement = self.cad_gate0_pos + (n * self.cad_gate_width)
            max_movement += (self.cad_last_gate_offset - self.cad_bypass_offset) if gate == self.GATE_BYPASS else 0.
        else:
            max_movement = self.cad_gate0_pos + (n * self.cad_gate_width) + (n//3) * self.cad_block_width
        max_movement += self.cal_tolerance
        return max_movement

    def _calibrate_selector(self, gate, save=True):
        gate_str = lambda gate : ("gate #%d" % gate) if gate >= 0 else "bypass"
        try:
            self._initialize_state()
            self.calibrating = True
            self._servo_move()
            max_movement = self._get_max_movement(gate)
            self._log_always("Measuring the selector position for %s. Up to %.1fmm" % (gate_str(gate), max_movement))
            traveled, found_home = self._measure_to_home(max_movement)

            # Test we actually homed, if not we didn't move far enough
            if not found_home:
                self._log_always("Selector didn't find home position. Are you sure you selected the correct gate?")
                return

            self._log_always("Selector move measured %.1fmm" % traveled)
            if save:
                if gate >= 0:
                    self.selector_offsets[gate] = round(traveled, 1)
                    self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=\"%s\"" % (self.VARS_MMU_SELECTOR_OFFSETS, self.selector_offsets))
                    self.calibration_status |= self.CALIBRATED_SELECTOR
                else:
                    self.bypass_offset = round(traveled, 1)
                    self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=\"%s\"" % (self.VARS_MMU_SELECTOR_BYPASS, self.bypass_offset))
                self._log_always("Selector offset (%.1fmm) for %s has been saved" % (traveled, gate_str(gate)))
        except MmuError as ee:
            self._pause(str(ee))
        finally:
            self.calibrating = False
            self._motors_off()

    def _calibrate_selector_auto(self, v1_bypass_block=-1, save=True):
        # Strategy is to find the two end gates, infer and set number of gates and distribute selector positions
        # Assumption: the user has manually positioned the selector aligned with gate #0 before calling
        try:
            self._log_always("Auto calibrating the selector. Excuse the whizz, bang, buzz, clicks...")
            self._initialize_state()
            self.calibrating = True
            self._servo_move()

            # Step 1 - position of gate #0
            self._log_always("Measuring the selector position for gate #0...")
            traveled, found_home = self._measure_to_home(self.cad_gate0_pos + self.cal_tolerance)
            if not found_home:
                self._log_always("Selector didn't find home position. Are you sure you aligned selector with gate #0 and removed filament?")
                return
            gate0_pos = traveled

            # Step 2 - end of selector
            max_movement = self._get_max_movement(self.cal_max_gates - 1)
            self._log_always("Searching for end of selector... (up to %.1fmm)" % max_movement)
            self.selector_stepper.do_set_position(0.)
            self._selector_stepper_move_wait(self.cad_gate0_pos) # Get off endstop
            if self.selector_touch:
                try:
                    self._selector_stepper_move_wait(max_movement, speed=self.selector_touch_speed, homing_move=1, endstop=self.SELECTOR_TOUCH_ENDSTOP)
                    found_home = True
                except Exception as e:
                    found_home = False
            else:
                # This might not sound good!
                self._selector_stepper_move_wait(max_movement, speed=self.selector_homing_speed)
                found_home = True
            if not found_home:
                self._log_always("Didn't detect the end of the selector")
                return

            # Step 3 - bypass (v2) and last gate position
            self._log_always("Measuring the full selector length...")
            traveled, found_home = self._measure_to_home(max_movement)
            if not found_home:
                self._log_always("Selector didn't find home position after full length move")
                return
            self._log_always("Maximum selector movement is %.1fmm" % traveled)
            bypass_pos = traveled - self.cad_bypass_offset
            last_gate_pos = traveled - self.cad_last_gate_offset

            # Step 4 - the calcs
            length = last_gate_pos - gate0_pos
            self._log_debug("Results: gate0_pos=%.1f, last_gate_pos=%.1f, length=%.1f" % (gate0_pos, last_gate_pos, length))
            self.selector_offsets = []
            if self.mmu_version >= 2.0:
                num_gates = int(round(length / self.cad_gate_width)) + 1
                adj_gate_width = length / (num_gates - 1)
                self._log_debug("Adjusted gate width: %.1f" % adj_gate_width)
                self.selector_offsets = []
                for i in range(num_gates):
                    self.selector_offsets.append(round(gate0_pos + (i * adj_gate_width), 1) for i in range(num_gates))
                self.bypass_offset = bypass_pos

            else:
                num_gates = int(round(length / (self.cad_gate_width + self.cad_block_width / 3))) + 1
                num_blocks = (num_gates - 1) // 3
                self.bypass_offset = 0.
                if v1_bypass_block >= 0:
                    adj_gate_width = (length - (num_blocks - 1) * self.cad_block_width - self.cad_bypass_block_width) / (num_gates - 1)
                else:
                    adj_gate_width = (length - num_blocks * self.cad_block_width) / (num_gates - 1)
                self._log_debug("Adjusted gate width: %.1f" % adj_gate_width)
                for i in range(num_gates):
                    bypass_adj = (self.cad_bypass_block_width - self.cad_block_width) if (i // 3) >= v1_bypass_block else 0.
                    self.selector_offsets.append(round(gate0_pos + (i * adj_gate_width) + (i // 3) * self.cad_block_width + bypass_adj, 1))
                    if ((i + 1) / 3) == v1_bypass_block:
                        self.bypass_offset = self.selector_offsets[i] + self.cad_bypass_block_delta

                if num_gates != self.num_gates:
                    self._log_error("You configued your MMU for %d gates but I counted %d! Please update `mmu_parameters`" % (self.num_gates, num_gates))
                    return

                self._log_always("Offsets %s and bypass %.1f" % (self.selector_offsets, self.bypass_offset))
                if save:
                    self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=\"%s\"" % (self.VARS_MMU_SELECTOR_OFFSETS, self.selector_offsets))
                    self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=\"%s\"" % (self.VARS_MMU_SELECTOR_BYPASS, self.bypass_offset))
                    self._log_always("Selector calibration has been saved for MMU %s v%s with %d gates" % (self.mmu_vendor, self.mmu_version_string, num_gates))
                    self.calibration_status |= self.CALIBRATED_SELECTOR

            self._home(0, force_unload=0)
        except MmuError as ee:
            self._pause(str(ee))
            self._motors_off()
        finally:
            self.calibrating = False

    def _sample_stats(self, values):
        mean = stdev = vmin = vmax = 0.
        if values:
            mean = sum(values) / len(values)
            diff2 = [( v - mean )**2 for v in values]
            stdev = math.sqrt( sum(diff2) / max((len(values) - 1), 1))
            vmin = min(values)
            vmax = max(values)
        return {'mean': mean, 'stdev': stdev, 'min': vmin, 'max': vmax, 'range': vmax - vmin}


### CALIBRATION GCODE COMMANDS

    cmd_MMU_CALIBRATE_GEAR_help = "Calibration routine for gear stepper rotational distance"
    def cmd_MMU_CALIBRATE_GEAR(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        length = gcmd.get_float('LENGTH', 100., above=50.)
        measured = gcmd.get_float('MEASURED', above=0.)
        save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)

        new_rotation_distance = self.ref_gear_rotation_distance * measured / length

        self._log_always("Gear stepper `rotation_distance` calculated to be %.6f" % new_rotation_distance)
        if save:
            self.gear_stepper.stepper.set_rotation_distance(new_rotation_distance)
            self.ref_gear_rotation_distance = new_rotation_distance
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%.6f" % (self.VARS_MMU_GEAR_ROTATION_DISTANCE, new_rotation_distance))
            self._log_always("Gear calibration has been saved for MMU %s v%s" % (self.mmu_vendor, self.mmu_version_string))
            self.calibration_status |= self.CALIBRATED_GEAR

    # Start: Assumes filament is loaded through encoder (e.g. with MMU_TEST_LOAD)
    # End: Does not eject filament at end (filament same as start)
    cmd_MMU_CALIBRATE_ENCODER_help = "Calibration routine for the MMU encoder"
    def cmd_MMU_CALIBRATE_ENCODER(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        if self._check_is_calibrated(self.CALIBRATED_GEAR): return

        length = gcmd.get_float('LENGTH', 400., above=0.)
        repeats = gcmd.get_int('REPEATS', 3, minval=1, maxval=10)
        speed = gcmd.get_float('SPEED', self.gear_from_buffer_speed, above=0.)
        min_speed = gcmd.get_float('MINSPEED', speed, above=0.)
        max_speed = gcmd.get_float('MAXSPEED', speed, above=0.)
        accel = gcmd.get_float('ACCEL', self.gear_stepper.accel, minval=0.)
        save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        try:
            self._servo_down()
            self.calibrating = True
            self._calibrate_encoder(length, repeats, speed, min_speed, max_speed, accel, save)
        except MmuError as ee:
            self._pause(str(ee))
        finally:
            self.calibrating = False

    cmd_MMU_CALIBRATE_SELECTOR_help = "Calibration of the selector positions or postion of specified gate"
    def cmd_MMU_CALIBRATE_SELECTOR(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return

        save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        gate = gcmd.get_int('GATE', -1, minval=0, maxval=self.num_gates - 1)
        if gate == -1:
            if gcmd.get_int('BYPASS', -1, minval=0, maxval=1) == 1:
                gate = self.TOOL_BYPASS

        if gate != -1:
            self._calibrate_selector(gate)
        else:
            v1_bypass_block = gcmd.get_int('BYPASS_BLOCK', -1, minval=1, maxval=3)
            self._calibrate_selector_auto(v1_bypass_block, save=save)

    # Start: Will home selector, select gate #0
    # End: Filament will unload
    cmd_MMU_CALIBRATE_BOWDEN_help = "Calibration of reference bowden length for gate #0"
    def cmd_MMU_CALIBRATE_BOWDEN(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        if self._check_is_calibrated(self.CALIBRATED_GEAR|self.CALIBRATED_ENCODER|self.CALIBRATED_SELECTOR): return

        approx_bowden_length = gcmd.get_float('BOWDEN_LENGTH', above=0.)
        repeats = gcmd.get_int('REPEATS', 3, minval=1, maxval=10)
        extruder_homing_max = gcmd.get_float('HOMING_MAX', 100, above=0.)
        save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        try:
            self._reset_ttg_mapping() # To force tool = gate
            self.calibrating = True
            self._home(0)
            self._calibrate_bowden_length(approx_bowden_length, extruder_homing_max, repeats, save)
        except MmuError as ee:
            self._pause(str(ee))
        finally:
            self.calibrating = False

    # Start: Will home selector, select gate #0 or required gate
    # End: Filament will unload
    cmd_MMU_CALIBRATE_GATES_help = "Optional calibration of individual MMU gate"
    def cmd_MMU_CALIBRATE_GATES(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        if self._check_is_calibrated(self.CALIBRATED_GEAR|self.CALIBRATED_ENCODER|self.CALIBRATED_SELECTOR|self.CALIBRATED_BOWDEN): return
        auto = gcmd.get_int('ALL', 0, minval=0, maxval=1)
        gate = gcmd.get_int('GATE', -1, minval=0, maxval=self.num_gates - 1)
        repeats = gcmd.get_int('REPEATS', 3, minval=1, maxval=10)
        save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        if gate == -1 and not auto:
            raise gcmd.error("Must specify 'GATE=' or 'ALL=1' for all gates")
        try:
            self._reset_ttg_mapping() # To force tool = gate
            self.calibrating = True
            if gate == -1:
                self._log_always("Start the complete calibration of ancillary gates...")
                self._home(0)
                for i in range(self.num_gates) - 1:
                    self._calibrate_gate(gate + 1, save=save)
                self._log_always("Phew! End of auto gate calibration")
            else:
                self._home(gate)
                self._calibrate_gate(gate, save=(save and gate != 0))
        except MmuError as ee:
            self._pause(str(ee))
        finally:
            self.calibrating = False


#######################
# MMU STATE FUNCTIONS #
#######################

    def _setup_heater_off_reactor(self):
        self.heater_off_handler = self.reactor.register_timer(self._handle_pause_timeout, self.reactor.NEVER)

    def _handle_pause_timeout(self, eventtime):
        self._log_info("Disable extruder heater")
        self.gcode.run_script_from_command("M104 S0")
        return self.reactor.NEVER

    def _handle_idle_timeout_printing(self, eventtime):
        if not self.is_enabled: return
        #self._log_trace("Processing idle_timeout Printing event")
        self._enable_encoder_sensor()

    def _handle_idle_timeout_ready(self, eventtime):
        if not self.is_enabled: return
        #self._log_trace("Processing idle_timeout Ready event")
        self._disable_encoder_sensor()

    def _handle_idle_timeout_idle(self, eventtime):
        if not self.is_enabled: return
        #self._log_trace("Processing idle_timeout Idle event")
        self._disable_encoder_sensor()

    def _pause(self, reason, force_in_print=False):
        run_pause = False
        self.paused_extruder_temp = self.printer.lookup_object(self.extruder_name).heater.target_temp
        if self._is_in_print() or force_in_print:
            if self.is_paused_locked: return
            self.is_paused_locked = True
            self._track_pause_start()
            self.gcode.run_script_from_command("SET_IDLE_TIMEOUT TIMEOUT=%d" % self.timeout_pause)
            self.reactor.update_timer(self.heater_off_handler, self.reactor.monotonic() + self.disable_heater)
            self._save_toolhead_position_and_lift()
            msg = "An issue with the MMU has been detected. Print paused"
            reason = "Reason: %s" % reason
            extra = "When you intervene to fix the issue, first call \'MMU_UNLOCK\'"
            run_pause = True
        elif self._is_in_pause():
            msg = "An issue with the MMU has been detected whilst printer is paused"
            reason = "Reason: %s" % reason
            extra = ""
        else:
            msg = "An issue with the MMU has been detected whilst out of a print"
            reason = "Reason: %s" % reason
            extra = ""

        self._sync_gear_to_extruder(False, servo=True)
        self._log_error("%s\n%s" % (msg, reason))
        if extra != "":
            self._log_always(extra)
        if run_pause:
            self.gcode.run_script_from_command("PAUSE")

    def _unlock(self):
        if not self.is_paused_locked: return
        self.reactor.update_timer(self.heater_off_handler, self.reactor.NEVER)
        if not self.printer.lookup_object(self.extruder_name).heater.can_extrude and self.paused_extruder_temp > 0:
            self._log_info("Enabling extruder heater (%.1f)" % self.paused_extruder_temp)
        self.gcode.run_script_from_command("M104 S%.1f" % self.paused_extruder_temp)
        self.encoder_sensor.reset_counts()    # Encoder 0000
        self.gcode.run_script_from_command("SET_IDLE_TIMEOUT TIMEOUT=%d" % self.timeout_unlock)
        self._track_pause_end()
        self.is_paused_locked = False
        self._disable_encoder_sensor() # Precautionary, should already be disabled

    def _save_toolhead_position_and_lift(self, remember=True):
        if remember and not self.saved_toolhead_position:
            self.toolhead.wait_moves()
            self._log_debug("Saving toolhead position")
            self.gcode.run_script_from_command("SAVE_GCODE_STATE NAME=MMU_state")
            self.saved_toolhead_position = True
        elif remember:
            self._log_debug("Asked to save toolhead position but it is already saved. Ignored")
            return
        else:
            self.saved_toolhead_position = False

        # Immediately lift toolhead off print
        if self.z_hop_height > 0:
            if 'z' not in self.toolhead.get_status(self.printer.get_reactor().monotonic())['homed_axes']:
                self._log_info("Warning: MMU cannot lift toolhead because toolhead not homed!")
            else:
                self._log_debug("Lifting toolhead %.1fmm" % self.z_hop_height)
                act_z = self.toolhead.get_position()[2]
                max_z = self.toolhead.get_status(self.printer.get_reactor().monotonic())['axis_maximum'].z
                safe_z = self.z_hop_height if (act_z < (max_z - self.z_hop_height)) else (max_z - act_z)
                self.toolhead.manual_move([None, None, act_z + safe_z], self.z_hop_speed)

    def _restore_toolhead_position(self):
        if self.saved_toolhead_position:
            self._log_debug("Restoring toolhead position")
            self.gcode.run_script_from_command("RESTORE_GCODE_STATE NAME=MMU_state MOVE=1 MOVE_SPEED=%.1f" % self.z_hop_speed)
        self.saved_toolhead_position = False

    def _disable_encoder_sensor(self, update_clog_detection_length=False):
        if self.encoder_sensor.is_enabled():
            self._log_debug("Disabled encoder sensor. Status: %s" % self.encoder_sensor.get_status(0))
            if update_clog_detection_length:
                self.encoder_sensor.update_clog_detection_length()
            self.encoder_sensor.disable()
            return True
        return False

    def _enable_encoder_sensor(self, restore=False):
        if restore or self._is_in_print():
            if not self.encoder_sensor.is_enabled():
                self.encoder_sensor.enable()
                self._log_debug("Enabled encoder sensor. Status: %s" % self.encoder_sensor.get_status(0))

    def _has_toolhead_sensor(self):
        return self.toolhead_sensor != None and self.toolhead_sensor.runout_helper.sensor_enabled

    def _must_home_to_extruder(self):
        return self.home_to_extruder or not self._has_toolhead_sensor()

    def _check_is_disabled(self):
        if not self.is_enabled:
            self._log_always("MMU is disabled. Please use MMU ENABLE=1 to use")
            return True
        return False

    def _check_is_paused(self):
        if self.is_paused_locked:
            self._log_always("MMU is currently locked/paused. Please use MMU_UNLOCK")
            return True
        return False

    def _check_in_bypass(self):
        if self.tool_selected == self.TOOL_BYPASS and self.loaded_status != self.LOADED_STATUS_UNLOADED:
            self._log_always("Operation not possible. MMU is currently using bypass. Unload or select a different gate first")
            return True
        return False

    def _check_not_bypass(self):
        if self.tool_selected != self.TOOL_BYPASS:
            self._log_always("Bypass not selected. Please use MMU_SELECT_BYPASS first")
            return True
        return False

    def _check_not_homed(self):
        if not self.is_homed:
            self._log_always("MMU is not homed")
            return True
        return False

    def _check_is_loaded(self):
        if not (self.loaded_status == self.LOADED_STATUS_UNLOADED or self.loaded_status == self.LOADED_STATUS_UNKNOWN):
            self._log_always("MMU has filament loaded")
            return True
        return False

    def _check_is_calibrated(self, required_calibration=None, silent=False):
        if not required_calibration:
            required_calibration = self.CALIBRATED_ALL
        if not (self.calibration_status & required_calibration == required_calibration):
            steps = []
            if self.CALIBRATED_GEAR & required_calibration:
                steps.append("MMU_CALIBRATE_GEAR")
            if self.CALIBRATED_ENCODER & required_calibration:
                steps.append("MMU_CALIBRATE_ENCODER")
            if self.CALIBRATED_SELECTOR & required_calibration:
                steps.append("MMU_CALIBRATE_SELECTOR")
            if self.CALIBRATED_BOWDEN & required_calibration:
                steps.append("MMU_CALIBRATE_BOWDEN")
            if self.CALIBRATED_GATES & required_calibration:
                steps.append("MMU_CALIBRATE_GATES")
            msg = "Prerequsite calibration steps are not complete. Please run:"
            if not silent:
                self._log_always("%s %s" % (msg, ",".join(steps)))
            return True
        return False

    def _is_in_print(self):
        return self._get_print_status() == "printing"

    def _is_in_pause(self):
        return self._get_print_status() == "paused"

    def _get_print_status(self):
        try:
            # If using virtual sdcard this is the most reliable method
            source = "print_stats"
            print_status = self.printer.lookup_object("print_stats").get_status(self.printer.get_reactor().monotonic())['state']
        except:
            # Otherwise we fallback to idle_timeout
            source = "idle_timeout"
            if self.printer.lookup_object("pause_resume").is_paused:
                print_status = "paused"
            else:
                idle_timeout = self.printer.lookup_object("idle_timeout").get_status(self.printer.get_reactor().monotonic())
                print_status = idle_timeout['state'].lower()
        finally:
            self._log_trace("Determined print status as: %s from %s" % (print_status, source))
            return print_status

    # Ensure we are above desired or min temperature and that target is set
    def _set_above_min_temp(self, target_temp=-1):
        extruder_heater = self.printer.lookup_object(self.extruder_name).heater
        current_temp = self.printer.lookup_object(self.extruder_name).get_status(0)['temperature']

        target_temp = max(target_temp, extruder_heater.target_temp, self.min_temp_extruder)
        new_target = False
        if extruder_heater.target_temp < target_temp:
            if (target_temp == self.min_temp_extruder):
                self._log_error("Heating extruder to minimum temp (%.1f)" % target_temp)
            else:
                self._log_info("Heating extruder to desired temp (%.1f)" % target_temp)
            self.gcode.run_script_from_command("SET_HEATER_TEMPERATURE HEATER=extruder TARGET=%.1f" % target_temp)
            new_target = True

        if current_temp < target_temp - 1:
            current_action = self._set_action(self.ACTION_HEATING)
            if not new_target:
                self._log_info("Waiting for extruder to reach temp (%.1f)" % target_temp)
            self.gcode.run_script_from_command("TEMPERATURE_WAIT SENSOR=extruder MINIMUM=%.1f MAXIMUM=%.1f" % (target_temp - 1, target_temp + 1))
            self._set_action(current_action)

    def _set_loaded_status(self, state, silent=False):
        self.loaded_status = state
        self._display_visual_state(silent=silent)

        # Minimal save_variable writes
        if state == self.LOADED_STATUS_FULL or state == self.LOADED_STATUS_UNLOADED:
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_LOADED_STATUS, state))
        elif self.variables.get(self.VARS_MMU_LOADED_STATUS, 0) != self.LOADED_STATUS_UNKNOWN:
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_LOADED_STATUS, self.LOADED_STATUS_UNKNOWN))

    def _selected_tool_string(self):
        if self.tool_selected == self.TOOL_BYPASS:
            return "bypass"
        elif self.tool_selected == self.TOOL_UNKNOWN:
            return "unknown"
        else:
            return "T%d" % self.tool_selected

    def _selected_gate_string(self):
        if self.gate_selected == self.TOOL_BYPASS:
            return "bypass"
        elif self.gate_selected == self.GATE_UNKNOWN:
            return "unknown"
        else:
            return "#%d" % self.gate_selected

    def _is_filament_in_bowden(self):
        if self.loaded_status == self.LOADED_STATUS_PARTIAL_PAST_ENCODER or self.loaded_status == self.LOADED_STATUS_PARTIAL_IN_BOWDEN:
            return True
        return False

    def _get_home_position_to_nozzle(self):
        if self._has_toolhead_sensor():
            if self.sensor_to_nozzle > 0.:
                return self.sensor_to_nozzle
        else:
            if self.extruder_to_nozzle > 0.:
                return self.extruder_to_nozzle
        return self.home_position_to_nozzle # Fallback to generic setting

    def _set_action(self, action):
        old_action = self.action
        self.action = action
        try:
            self.printer.lookup_object('gcode_macro _MMU_ACTION_CHANGED')
            self.gcode.run_script_from_command("_MMU_ACTION_CHANGED")
        finally:
            return old_action


### STATE GCODE COMMANDS

    cmd_MMU_help = "Enable/Disable functionality and reset state"
    def cmd_MMU(self, gcmd):
        enable = gcmd.get_int('ENABLE', minval=0, maxval=1)
        if enable == 1 and not self.is_enabled:
            self._log_always("MMU enabled and reset")
            self._initialize_state()
            if not self._check_is_calibrated(silent=True):
                self._load_persisted_state()
            self._log_always(self._tool_to_gate_map_to_human_string(summary=True))
            self._display_visual_state()
        elif enable == 0 and self.is_enabled:
            self._log_always("MMU disabled")
            self.is_enabled = False

    cmd_MMU_HELP_help = "Display the complete set of MMU commands and function"
    def cmd_MMU_HELP(self, gcmd):
        testing = gcmd.get_int('TESTING', 0, minval=0, maxval=1)
        macros = gcmd.get_int('MACROS', 0, minval=0, maxval=1)
        msg = "Happy Hare MMU commands: (use MMU_HELP MACROS=1 TESTING=1 for full command set)\n"
        tmsg = "\nCalibration and testing commands:\n"
        mmsg = "\nMacros and callbacks (defined in mmu_software.cfg):\n"
        cmds = list(self.gcode.ready_gcode_handlers.keys())
        cmds.sort()
        for c in cmds:
            d = self.gcode.gcode_help.get(c, "n/a")
            if c.startswith("MMU") and not c.startswith("MMU__"):
                if not "_CALIBRATE" in c and not "_TEST" in c and not "_SOAKTEST" in c:
                    msg += "%s : %s\n" % (c.upper(), d)
                else:
                    tmsg += "%s : %s\n" % (c.upper(), d)
            elif c.startswith("_MMU"):
                mmsg += "%s : %s\n" % (c.upper(), d)
        if testing:
            msg += tmsg
        if macros:
            msg += mmsg
        self._log_always(msg)

    cmd_MMU_ENCODER_help = "Display encoder position or temporarily enable/disable detection logic in encoder"
    def cmd_MMU_ENCODER(self, gcmd):
        if self._check_is_disabled(): return
        enable = gcmd.get_int('ENABLE', -1, minval=0, maxval=1)
        if enable == 1:
            self._enable_encoder_sensor(True)
        elif enable == 0:
            self._disable_encoder_sensor(True)
        else:
            self._log_info("Encoder value is %.2f" % self.encoder_sensor.get_distance())

    cmd_MMU_RESET_help = "Forget persisted state and re-initialize defaults"
    def cmd_MMU_RESET(self, gcmd):
        confirm = gcmd.get_int('CONFIRM', 0, minval=0, maxval=1)
        if confirm != 1:
            self._log_always("You must re-run and add 'CONFIRM=1' to reset all state back to default")
            return
        self._initialize_state()
        self.enable_endless_spool = self.default_enable_endless_spool
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_ENABLE_ENDLESS_SPOOL, self.enable_endless_spool))
        self.endless_spool_groups = list(self.default_endless_spool_groups)
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_ENDLESS_SPOOL_GROUPS, self.endless_spool_groups))
        self.tool_to_gate_map = list(self.default_tool_to_gate_map)
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_TOOL_TO_GATE_MAP, self.tool_to_gate_map))
        self.gate_status = list(self.default_gate_status)
        self.gate_material = list(self.default_gate_material)
        self.gate_color = list(self.default_gate_color)
        self._persist_gate_map()
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_GATE_SELECTED, self.gate_selected))
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_TOOL_SELECTED, self.tool_selected))
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_LOADED_STATUS, self.loaded_status))
        self._log_always("MMU state reset")


####################################################################################
# GENERAL MOTOR HELPERS - All stepper movements should go through here for tracing #
####################################################################################

    def _gear_stepper_move_wait(self, dist, wait=True, speed=None, accel=None, sync=True):
        self._sync_gear_to_extruder(False) # Safety
        is_long_move = abs(dist) > self.LONG_MOVE_THRESHOLD
        if speed is None:
            if is_long_move:
                if (dist > 0 and self.gate_selected >= 0 and self.gate_status[self.gate_selected] != self.GATE_AVAILABLE_FROM_BUFFER):
                    # Long pulling move when we are sure that we are at a gate but the filament buffer might be empty
                    speed = self.gear_from_spool_speed
                else:
                    speed = self.gear_from_buffer_speed
            else:
                speed = self.gear_short_move_speed
        accel = accel or self.gear_stepper.accel

        self.gear_stepper.do_set_position(0.)   # All gear moves are relative
        self._log_stepper("GEAR: dist=%.1f, speed=%.1f, accel=%.1f sync=%s wait=%s" % (dist, speed, accel, sync, wait))
        self.gear_stepper.do_move(dist, speed, accel, sync)
        if wait:
            self.toolhead.wait_moves()

    # Convenience wrapper around all gear and extruder motor movement that tracks measured movement and create trace log entry
    # motor = "gear" - gear only
    #         "extruder" - extruder only
    #         "both" - gear and extruder together but independent (legacy, homing move not possible))
    #         "gear+extruder" - gear driving and extruder linked
    #         "extruder+gear" - extruder driving and gear linked
    # If homing move then endstop name can be specified.
    # Homing moves return (homed, actual_movement, measured_movement)
    # Nomal moves return measured_movement
    # All move distances are interpreted as relative and stepper commanded position will be reset
    def _trace_filament_move(self, trace_str, dist, speed=None, accel=None, motor="gear", homing_move=0, endstop="default", track=False):
        self._sync_gear_to_extruder(False) # Safety
        start = self.encoder_sensor.get_distance()
        homed = False
        actual = 0.

        if homing_move != 0:
            # Generally homing speeds/accel are set here
            if motor == "both" or motor == "gear+extruder" or motor == "extruder+gear":
                speed = speed or min(self.gear_homing_speed, self.extruder_homing_speed)
                accel = accel or min(self.gear_stepper.accel, self.extruder_accel)
            elif motor == "extruder":
                speed = speed or self.extruder_homing_speed
                accel = accel or self.extruder_accel
            else:
                speed = speed or self.gear_homing_speed
                accel = accel or self.gear_stepper.accel
        else:
            # Generally the speed/accel will be specified but here are some saftely values
            if motor == "both" or motor == "gear+extruder" or motor == "extruder+gear":
                speed = speed or self.extruder_sync_load_speed
                accel = accel or min(self.gear_stepper.accel, self.extruder_accel)
            elif motor == "extruder":
                speed = speed or self.extruder_load_speed
            else:
                pass # Set later based on a few factors

        if motor == "both":
            if homing_move != 0:
                self._log_error("Not possible to perform homing move on two independent steppers")
            else:
                self._log_stepper("BOTH: dist=%.1f, speed=%.1f, accel=%.1f" % (dist, speed, accel))
                self.gear_stepper.do_set_position(0.) # Force relative move
                pos = self.toolhead.get_position()
                pos[3] += dist
                self.gear_stepper.do_move(dist, speed, accel, False)
                self.toolhead.manual_move(pos, speed)
                self.toolhead.dwell(0.05) # "MCU Timer too close" protection
                self.toolhead.wait_moves()
                self.toolhead.set_position(pos) # Force subsequent incremental move

        elif motor == "gear":
            if homing_move != 0:
                self._log_stepper("GEAR HOME: dist=%.1f, speed=%.1f, accel=%.1f, endstop=%s" % (dist, speed, accel, endstop))
                try:
                    self.gear_stepper.do_set_position(0.) # Force relative move
                    init_mcu_pos = self.gear_stepper.stepper.get_mcu_position()
                    self.gear_stepper.do_mh_homing_move(dist, speed, accel, homing_move > 0, abs(homing_move) == 1, endstop_name=endstop)
                    homed = True
                except Exception as e:
                    self._log_stepper("Did not home: %s" % str(e))
                finally:
                    actual = (self.gear_stepper.stepper.get_mcu_position() - init_mcu_pos) * self.gear_stepper.stepper.get_step_dist()
            else:
                self._gear_stepper_move_wait(dist, speed=speed, accel=accel, wait=True)

        elif motor == "extruder":
            if homing_move != 0:
                self._log_stepper("EXTRUDER HOME: dist=%.1f, speed=%.1f, accel=%.1f, endstop=%s" % (dist, speed, accel, endstop))
                try:
                    self.mmu_extruder_stepper.sync_to_extruder(None) # Unsync extruder stepper from toolhead motion queue
                    self.mmu_extruder_stepper.do_set_position(0.) # Force relative move
                    init_mcu_pos = self.mmu_extruder_stepper.stepper.get_mcu_position()
                    self.mmu_extruder_stepper.do_mh_homing_move(dist,
                            speed, accel, homing_move > 0, abs(homing_move) == 1, endstop_name=endstop)
                    homed = True
                except Exception as e:
                    self._log_stepper("Did not home: %s" % str(e))
                finally:
                    actual = (self.mmu_extruder_stepper.stepper.get_mcu_position() - init_mcu_pos) * self.mmu_extruder_stepper.stepper.get_step_dist()
                    self.mmu_extruder_stepper.sync_to_extruder(self.extruder_name) # Sync back to toolhead
            else:
                self._log_stepper("EXTRUDER: dist=%.1f, speed=%.1f" % (dist, speed))
                pos = self.toolhead.get_position()
                pos[3] += dist
                self.toolhead.manual_move(pos, speed)
                self.toolhead.wait_moves()
                self.toolhead.set_position(pos) # Force subsequent incremental move

        elif motor == "gear+extruder":
            if homing_move != 0:
                self._log_stepper("GEAR+EXTRUDER HOME: dist=%.1f, speed=%.1f, accel=%.1f, endstop=%s, linked_stepper=%s" % (dist, speed, accel, endstop, self.extruder_name))
                try:
                    self.gear_stepper.do_set_position(0.) # Force relative move
                    init_mcu_pos = self.gear_stepper.stepper.get_mcu_position()
                    self.gear_stepper.do_linked_homing_move(dist,
                            speed, accel, homing_move > 0, abs(homing_move) == 1, endstop_name=endstop, linked_extruder=self.extruder_name)
                    homed = True
                except Exception as e:
                    self._log_stepper("Did not home: %s" % str(e))
                finally:
                    actual = (self.gear_stepper.stepper.get_mcu_position() - init_mcu_pos) * self.gear_stepper.stepper.get_step_dist()
            else:
                self._log_stepper("GEAR+EXTRUDER: dist=%.1f, speed=%.1f, accel=%.1f, linked_stepper=%s" % (dist, speed, accel, self.extruder_name))
                self.gear_stepper.do_set_position(0.) # Force relative move
                self.gear_stepper.do_linked_move(dist, speed, accel, linked_extruder=self.extruder_name)
                self.toolhead.wait_moves()

        elif motor == "extruder+gear":
            if homing_move != 0:
                self._log_stepper("EXTRUDER+GEAR HOME: dist=%.1f, speed=%.1f, accel=%.1f, endstop=%s, linked_stepper=%s" % (dist, speed, accel, endstop, "gear_stepper"))
                try:
                    self.mmu_extruder_stepper.sync_to_extruder(None) # Disssociate/unsync with toolhead
                    self.mmu_extruder_stepper.do_set_position(0.) # Force relative move
                    init_mcu_pos = self.mmu_extruder_stepper.stepper.get_mcu_position()
                    self.mmu_extruder_stepper.do_linked_homing_move(dist,
                            speed, accel, homing_move > 0, abs(homing_move) == 1, endstop_name=endstop, linked_extruder="gear_stepper")
                    homed = True
                except Exception as e:
                    self._log_stepper("Did not home: %s" % str(e))
                finally:
                    actual = (self.mmu_extruder_stepper.stepper.get_mcu_position() - init_mcu_pos) * self.mmu_extruder_stepper.stepper.get_step_dist()
                    self.mmu_extruder_stepper.sync_to_extruder(self.extruder_name) # Restore toolhead association/syncing
            else:
                self._log_stepper("EXTRUDER+GEAR: dist=%.1f, speed=%.1f, accel=%.1f, linked_stepper=%s" % (dist, speed, accel, "gear_stepper"))
                self.mmu_extruder_stepper.sync_to_extruder(None) # Disssociate/unsync with toolhead
                self.mmu_extruder_stepper.do_set_position(0.) # Force relative move
                self.mmu_extruder_stepper.do_linked_move(dist, speed, accel, linked_extruder="gear_stepper")
                self.toolhead.wait_moves()
                self.mmu_extruder_stepper.sync_to_extruder(self.extruder_name) # Restore toolhead association/syncing

        else:
            self._log_error("Assertion Failure: Invalid motor specification")

        end = self.encoder_sensor.get_distance()
        measured = end - start
        if homing_move != 0:
            delta = abs(actual) - measured # +ve means measured less than moved, -ve means measured more than moved
            trace_str += ". Stepper: '%s' %s after moving %.1fmm (of max %.1fmm), encoder measured %.1fmm (delta %.1fmm)"
            self._log_trace(trace_str % (motor, ("homed" if homed else "did not home"), actual, dist, measured, delta))
        else:
            delta = abs(dist) - measured # +ve means measured less than moved, -ve means measured more than moved
            trace_str += ". Stepper: '%s' moved %.1fmm, encoder measured %.1fmm (delta %.1fmm)"
            self._log_trace(trace_str % (motor, dist, measured, delta))
        trace_str += ". Counter: @%.1fmm" % end

        if motor == "gear" and track:
            if dist > 0:
                self._track_gate_statistics('load_distance', self.gate_selected, dist)
                self._track_gate_statistics('load_delta', self.gate_selected, delta)
            else:
                self._track_gate_statistics('unload_distance', self.gate_selected, -dist)
                self._track_gate_statistics('unload_delta', self.gate_selected, delta)

        if homing_move != 0:
            return homed, actual, delta
        else:
            return delta

    def _selector_stepper_move_wait(self, dist, wait=True, speed=None, accel=None, homing_move=0, endstop="default"):
        speed = speed or self.selector_move_speed
        speed = min(self.selector_stepper.velocity, speed) # Cap max speed
        accel = accel or self.selector_stepper.accel
        if homing_move != 0:
            self._log_stepper("SELECTOR: dist=%.1f, speed=%.1f, accel=%.1f homing_move=%d, endstop=%s" % (dist, speed, accel, homing_move, endstop))
            # Don't allow stallguard home moves in rapid succession (TMC limitation)
            if self.selector_touch:
                current_time = self.estimated_print_time(self.reactor.monotonic())
                time_since_last = self.last_selector_move_time + 1.0 - current_time # 1 sec recovery time
                if (time_since_last) > 0:
                    self._log_trace("Waiting %.2f seconds before next sensorless homing move" % time_since_last)
                    self.toolhead.dwell(time_since_last)
            elif abs(dist - self.selector_stepper.get_position()[0]) < 12: # Workaround for Timer Too Close error with short homing moves
                self.toolhead.dwell(1)
            self.selector_stepper.do_mh_homing_move(dist, speed, accel, homing_move > 0, abs(homing_move) == 1, endstop_name=endstop)
        else:
            self._log_stepper("SELECTOR: dist=%.1f, speed=%.1f, accel=%.1f" % (dist, speed, accel))
            self.selector_stepper.do_move(dist, speed, accel)
        if wait:
            self.toolhead.wait_moves()
        self.last_selector_move_time = self.estimated_print_time(self.reactor.monotonic())

    def _buzz_gear_motor(self):
        initial_encoder_position = self.encoder_sensor.get_distance()
        self._gear_stepper_move_wait(2.5, accel=self.gear_buzz_accel, wait=False)
        self._gear_stepper_move_wait(-2.5, accel=self.gear_buzz_accel)
        delta = self.encoder_sensor.get_distance() - initial_encoder_position
        self._log_trace("After buzzing gear motor, encoder moved %.2f" % delta)
        self.encoder_sensor.set_distance(initial_encoder_position)
        return delta > self.encoder_min

    # Check for filament in encoder by wiggling MMU gear stepper and looking for movement on encoder
    def _check_filament_in_encoder(self):
        self._log_debug("Checking for filament in encoder...")
        if self._check_toolhead_sensor() == 1:
            self._log_debug("Filament must be in encoder because reported in extruder by toolhead sensor")
            return True
        self._servo_down()
        found = self._buzz_gear_motor()
        self._log_debug("Filament %s in encoder after buzzing gear motor" % ("detected" if found else "not detected"))
        return found

    # Return toolhead sensor or -1 if not installed
    def _check_toolhead_sensor(self):
        if self._has_toolhead_sensor():
            if self.toolhead_sensor.runout_helper.filament_present:
                self._log_trace("(Toolhead sensor detects filament)")
                return 1
            else:
                self._log_trace("(Toolhead sensor does not detect filament)")
                return 0
        return -1

    # Check for filament in extruder by moving extruder motor. This is only used with toolhead sensor
    # and can only happen if filament is the short distance from sensor to gears. This check will eliminate that
    # problem and indicate if we can unload the rest of the bowden more quickly
    def _check_filament_still_in_extruder(self):
        self._log_info("Checking for possibility of filament still in extruder gears...")
        self._set_above_min_temp()
        self._servo_up()
        length = self.encoder_move_step_size
        delta = self._trace_filament_move("Checking extruder", -length, speed=self.extruder_unload_speed, motor="extruder")
        return (length - delta) > self.encoder_min

    def _sync_gear_to_extruder(self, sync=True, servo=False, in_print=False):
        if servo:
            if sync:
                self._servo_down()
            else:
                self._servo_auto()
        if self.gear_stepper.is_synced() != sync:
            self._log_debug("%s gear stepper and extruder" % ("Syncing" if sync else "Unsyncing"))
            self.gear_stepper.sync_to_extruder(self.extruder_name if sync else None)

        # Option to reduce current during print
        if in_print and sync and self.gear_tmc and self.sync_gear_current < 100 and self.gear_stepper_run_current < 0 :
            self.gear_stepper_run_current = self.gear_tmc.get_status(0)['run_current']
            self._log_info("Reducing reducing gear_stepper run current to %d%% for extruder syncing" % self.sync_gear_current)
            self.gcode.run_script_from_command("SET_TMC_CURRENT STEPPER=gear_stepper CURRENT=%.2f"
                                                % ((self.gear_stepper_run_current * self.sync_gear_current) / 100.))
        elif self.gear_tmc and self.gear_tmc.get_status(0)['run_current'] < self.gear_stepper_run_current:
            self._log_info("Restoring gear_stepper run current to 100% configured")
            self.gcode.run_script_from_command("SET_TMC_CURRENT STEPPER=gear_stepper CURRENT=%.2f" % self.gear_stepper_run_current)
            self.gear_stepper_run_current = -1


###########################
# FILAMENT LOAD FUNCTIONS #
###########################

    # Primary method to selects and loads tool. Assumes we are unloaded.
    def _select_and_load_tool(self, tool):
        self._log_debug('Loading tool T%d...' % tool)
        self._select_tool(tool, move_servo=False)
        gate = self.tool_to_gate_map[tool]
        if self.gate_status[gate] == self.GATE_EMPTY:
            raise MmuError("Gate %d is empty!" % gate)
        self._load_sequence(self.calibrated_bowden_length)

        # Ready to print...
        self._sync_gear_to_extruder(self.sync_to_extruder, servo=True, in_print=True)

    def _load_sequence(self, length, no_extruder = False):
        current_action = self._set_action(self.ACTION_LOADING)
        try:
            self._log_info("Loading filament...")
            self.filament_direction = self.DIRECTION_LOAD
            self._set_loaded_status(self.LOADED_STATUS_UNLOADED)
            # If full length load requested then assume homing is required (if configured)
            if (length >= self.calibrated_bowden_length):
                if (length > self.calibrated_bowden_length):
                    length = self.calibrated_bowden_length
                    self._log_info("Restricting load length to extruder calibration reference of %.1fmm" % length)
                home = True
            else:
                home = False

            self.toolhead.wait_moves()
            self.encoder_sensor.reset_counts()    # Encoder 0000
            self._track_load_start()
            encoder_measured = self._load_encoder()
            if length - encoder_measured > 0:
                self._load_bowden(length - encoder_measured)

            if home:
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_END_OF_BOWDEN)
                self._log_debug("Full length load, will home filament...")
                if self._must_home_to_extruder():
                    self._home_to_extruder(self.extruder_homing_max)
                if not no_extruder:
                    self._load_extruder()

            self.toolhead.wait_moves()
            self._log_info("Loaded %.1fmm of filament" % self.encoder_sensor.get_distance())
        except MmuError as ee:
            self._track_gate_statistics('load_failures', self.gate_selected)
            raise MmuError(ee)
        finally:
            self._track_load_end()
            self._set_action(current_action)

    # Step 1 of the load sequence
    # Load filament past encoder and return the actual measured distance detected by encoder
    # Returns the measured filament distance moved
    def _load_encoder(self, retry=True, adjust_servo_on_error=True):
        self.filament_direction = self.DIRECTION_LOAD
        self._servo_down()
        initial_encoder_position = self.encoder_sensor.get_distance()
        retries = self.load_encoder_retries if retry else 1
        for i in range(retries):
            msg = "Initial load into encoder" if i == 0 else ("Retry load into encoder #%d" % i)
            delta = self._trace_filament_move(msg, self.LONG_MOVE_THRESHOLD)
            if (self.LONG_MOVE_THRESHOLD - delta) > 6.0:
                self._set_gate_status(self.gate_selected, max(self.gate_status[self.gate_selected], self.GATE_AVAILABLE)) # Don't reset if filament is buffered
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_PAST_ENCODER)
                return self.encoder_sensor.get_distance() - initial_encoder_position
            else:
                self._log_debug("Error loading filament - not enough detected at encoder. %s" % ("Retrying..." if i < retries - 1 else ""))
                if i < retries - 1:
                    self._track_gate_statistics('servo_retries', self.gate_selected)
                    self._servo_up()
                    self._servo_down()
        self._set_gate_status(self.gate_selected, self.GATE_UNKNOWN)
        self._set_loaded_status(self.LOADED_STATUS_UNLOADED)
        if adjust_servo_on_error:
            self._servo_auto()
        raise MmuError("Error picking up filament at gate - not enough movement detected at encoder")

    # Step 2 of the load sequence
    # Fast load of filament to approximate end of bowden (without homing)
    # Returns the measured distance moved if encoder equipped else actual
    def _load_bowden(self, length):
        self._log_debug("Loading bowden tube")
        tolerance = self.load_bowden_tolerance
        self.filament_direction = self.DIRECTION_LOAD
        self._servo_down()

        # Fast load
        moves = 1 if length < (self.calibrated_bowden_length / self.num_moves) else self.num_moves
        delta = 0
        for i in range(moves):
            msg = "Course loading move #%d into bowden" % (i+1)
            delta += self._trace_filament_move(msg, length / moves, track=True)
            self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_BOWDEN)

        # Correction attempts to load the filament according to encoder reporting
        if delta >= tolerance and not self.calibrating:
            if self.apply_bowden_correction:
                for i in range(2):
                    if delta >= tolerance:
                        msg = "Correction load move #%d into bowden" % (i+1)
                        delta = self._trace_filament_move(msg, delta, track=True)
                        self._log_debug("Correction load move was necessary, encoder now measures %.1fmm" % self.encoder_sensor.get_distance())
                    else:
                        break
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_BOWDEN)
                if delta >= tolerance:
                    self._log_info("Warning: Excess slippage was detected in bowden tube load afer correction moves. Gear moved %.1fmm, Encoder delta %.1fmm. See mmu.log for more details"% (length, delta))
            else:
                self._log_info("Warning: Excess slippage was detected in bowden tube load but 'apply_bowden_correction' is disabled. Gear moved %.1fmm, Encoder delta %.1fmm. See mmu.log for more details" % (length, delta))

            if delta >= tolerance:
                self._log_debug("Possible causes of slippage:\nCalibration ref length too long (hitting extruder gear before homing)\nCalibration ratio for gate is not accurate\nMMU gears are not properly gripping filament\nEncoder reading is inaccurate\nFaulty servo\nLoad speed too fast for encoder to track (>200mm/s)")
        return length - delta

    # Step 3a of the load sequence
    # This optional step snugs the filament up to the extruder gears
    # Returns the measured distance moved for collision else actual from klipper
    def _home_to_extruder(self, max_length):
        self._servo_down()
        self.filament_direction = self.DIRECTION_LOAD
        try:
            self.mmu_extruder_stepper.sync_to_extruder(None) # Unsync extruder stepper from toolhead so we can force enable to lock motors
            self.mmu_extruder_stepper.do_enable(True)

            if self.extruder_homing_endstop != self.EXTRUDER_COLLISION_ENDSTOP:
                self._log_debug("Homing to extruder '%s' endstop, up to %.1fmm" % (self.extruder_homing_endstop, max_length))
                homed, actual, delta = self._trace_filament_move("Homing filament to extruder",
                        max_length, motor="gear", homing_move=1, endstop=self.extruder_homing_endstop)
                measured_movement = max_length - delta
                reported_movement = actual
                if homed:
                    self._log_debug("Extruder entrance reached after %.1fmm (measured %.1fmm)" % (actual, measured_movement))
            else:
                homed, measured_movement = self._home_to_extruder_collision_detection(max_length)
                reported_movement = measured_movement

            if not homed:
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_END_OF_BOWDEN)
                raise MmuError("Failed to reach extruder gear after moving %.1fmm" % max_length)

            if measured_movement > (max_length * 0.8):
                self._log_info("Warning: 80% of 'extruder_homing_max' was used homing. You may want to increase your initial load distance ('%s') or increase 'extruder_homing_max'" % self.VARS_MMU_CALIB_BOWDEN_LENGTH)
            self._set_loaded_status(self.LOADED_STATUS_PARTIAL_HOMED_EXTRUDER)
            return reported_movement
        finally:
            self.mmu_extruder_stepper.sync_to_extruder(self.extruder_name) # Resync with toolhead extruder

    def _home_to_extruder_collision_detection(self, max_length):
        step = self.encoder_min_resolution * self.extruder_collision_homing_step
        self._log_debug("Homing to extruder gear, up to %.1fmm in %.1fmm steps" % (max_length, step))

        if self.gear_tmc and self.extruder_homing_current < 100:
            run_current = self.gear_tmc.get_status(0)['run_current']
            self._log_debug("Temporarily reducing gear_stepper run current to %d%% for collision detection"
                                % self.extruder_homing_current)
            self.gcode.run_script_from_command("SET_TMC_CURRENT STEPPER=gear_stepper CURRENT=%.2f"
                                                % ((run_current * self.extruder_homing_current) / 100.))

        initial_encoder_position = self.encoder_sensor.get_distance()
        homed = False
        for i in range(int(max_length / step)):
            msg = "Homing step #%d" % (i+1)
            delta = self._trace_filament_move(msg, step, speed=self.gear_homing_speed)
            measured_movement = self.encoder_sensor.get_distance() - initial_encoder_position
            total_delta = step*(i+1) - measured_movement
            if delta >= self.encoder_min or abs(total_delta) > step: # Not enough or strange measured movement means we've hit the extruder
                homed = True
                break
        self._log_debug("Extruder entrance%s found after %.1fmm move (%d steps), encoder measured %.1fmm (total_delta %.1fmm)"
                % (" not" if not homed else "", step*(i+1), i+1, measured_movement, total_delta))

        if self.gear_tmc and self.extruder_homing_current < 100:
            self.gcode.run_script_from_command("SET_TMC_CURRENT STEPPER=gear_stepper CURRENT=%.2f" % run_current)

        if total_delta > 5.0:
            self._log_info("Warning: A lot of slippage was detected whilst homing to extruder, you may want to reduce 'extruder_homing_current' and/or ensure a good grip on filament by gear drive")
        return homed, measured_movement

    # Step 3b of the load sequence
    # This optional step aligns (homes) filament to the toolhead sensor which should be a very
    # reliable location. Returns measured movement
    # 'extruder_stepper_only' forces only extruder stepper movement regardless of configuration
    def _home_to_toolhead_sensor(self, extruder_stepper_only):
        if self.toolhead_sensor.runout_helper.filament_present:
            # We shouldn't be here and probably means the toolhead sensor is malfunctioning/blocked
            raise MmuError("Toolhead sensor malfunction - filament detected before it entered extruder!")

        sync = self.sync_load_extruder and not extruder_stepper_only
        self._log_debug("Homing up to %.1fmm to toolhead sensor%s" % (self.toolhead_homing_max, (" (synced)" if sync else "")))

        if sync:
            homed, actual, delta = self._trace_filament_move("Synchronously homing to toolhead sensor",
                    self.toolhead_homing_max, motor="gear+extruder", homing_move=1, endstop="mmu_toolhead")
        else:
            length = self.toolhead_homing_max
            if not extruder_stepper_only:
                # Extruder transition moves...
                if self.delay_servo_release > 0 and self._must_home_to_extruder():
                    # Delay servo release by a few mm to keep filament tension for reliable transition
                    delta = self._trace_filament_move("Small extruder move under filament tension before servo release",
                            self.delay_servo_release, speed=self.extruder_load_speed, motor="extruder")
                    length -= self.delay_servo_release
                if self.transition_length > 0:
                    # Synced extruder transistion move
                    dist = max(0, self.transition_length - self.delay_servo_release)
                    homed, actual, delta = self._trace_filament_move("Synced extruder entry move",
                            dist, motor="gear+extruder", homing_move=1, endstop="mmu_toolhead")
                    length -= dist
            if not self.toolhead_sensor.runout_helper.filament_present:
                # Now complete homing with just extruder movement
                self._servo_up()
                homed, actual, delta = self._trace_filament_move("Homing to toolhead sensor",
                        length, motor="extruder", homing_move=1, endstop="mmu_toolhead")

        if self.toolhead_sensor.runout_helper.filament_present:
            self._set_loaded_status(self.LOADED_STATUS_PARTIAL_HOMED_SENSOR)
        else:
            raise MmuError("Failed to reach toolhead sensor after moving %.1fmm" % self.toolhead_homing_max)

    # Step 4 of the load sequence
    # Move filament from the home position (extruder or sensor) to the nozzle. Return measured movement
    def _load_extruder(self, extruder_stepper_only=False):
        current_action = self._set_action(self.ACTION_LOADING_EXTRUDER)
        try:
            self.filament_direction = self.DIRECTION_LOAD
            self._set_above_min_temp()

            if self._has_toolhead_sensor():
                # With toolhead sensor we home to toolhead sensor past the extruder entrance
                self._home_to_toolhead_sensor(extruder_stepper_only)

            length = self._get_home_position_to_nozzle()
            self._log_debug("Loading last %.1fmm to the nozzle..." % length)
            initial_encoder_position = self.encoder_sensor.get_distance()

            if self.sync_load_extruder and not extruder_stepper_only:
                self._servo_down()
                delta = self._trace_filament_move("Synchronously loading filament to nozzle", length,
                        speed=self.extruder_sync_load_speed, motor="gear+extruder")
            else:
                if not self._has_toolhead_sensor() and not extruder_stepper_only:
                    # Extruder transition moves...
                    if self.delay_servo_release > 0:
                        # Delay servo release by a few mm to keep filament tension for reliable transition
                        delta = self._trace_filament_move("Small extruder move under filament tension before servo release",
                                self.delay_servo_release, speed=self.extruder_load_speed, motor="extruder")
                        length -= self.delay_servo_release
                    if self.transition_length > 0:
                        # Synced extruder transistion move
                        delta = self._trace_filament_move("Synced extruder entry move",
                                self.transition_length - self.delay_servo_release, speed=self.extruder_sync_load_speed, motor="gear+extruder")
                        length -= (self.transition_length - self.delay_servo_release)

                # Move the remaining distance to the nozzle meltzone under exclusive extruder stepper control
                self._servo_up()
                delta = self._trace_filament_move("Remainder of final move to mozzle", length,
                        speed=self.extruder_load_speed, motor="extruder")

            # Final sanity check
            measured_movement = self.encoder_sensor.get_distance() - initial_encoder_position
            total_delta = self._get_home_position_to_nozzle() - measured_movement
            self._log_debug("Total measured movement: %.1fmm, total delta: %.1fmm" % (measured_movement, total_delta))
            tolerance = max(self.encoder_sensor.get_clog_detection_length(), self._get_home_position_to_nozzle() * 0.50)
            if total_delta > tolerance:
                msg = "Move to nozzle failed (encoder not sensing sufficient movement). Extruder may not have picked up filament or filament did not home correctly"
                if not self.ignore_extruder_load_error:
                    self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_EXTRUDER)
                    raise MmuError(msg)
                else:
                   self._log_always("Ignoring: %s" % msg)

            self._set_loaded_status(self.LOADED_STATUS_FULL)
            self._log_info('MMU load successful')

        finally:
            self._set_action(current_action)

#############################
# FILAMENT UNLOAD FUNCTIONS #
#############################

    # Primary method to unload current tool but retains selection
    def _unload_tool(self, skip_tip=False):
        if self._check_is_paused(): return
        if self.loaded_status == self.LOADED_STATUS_UNLOADED:
            self._log_debug("Tool already unloaded")
            return
        self._log_debug("Unloading tool %s" % self._selected_tool_string())
        self._unload_sequence(self.calibrated_bowden_length, skip_tip=skip_tip)

    def _unload_sequence(self, length, check_state=False, skip_sync_move=False, skip_tip=False):
        current_action = self._set_action(self.ACTION_UNLOADING)
        try:
            self.filament_direction = self.DIRECTION_UNLOAD
            self.toolhead.wait_moves()
            self.encoder_sensor.reset_counts()    # Encoder 0000
            self._track_unload_start()

            if check_state or self.loaded_status == self.LOADED_STATUS_UNKNOWN:
                # Let's determine where filament is and reset state before continuing
                self._log_error("Unsure of filament position, recovering state...")
                self._recover_loaded_state()

            if self.loaded_status == self.LOADED_STATUS_UNLOADED:
                self._log_debug("Filament already ejected")
                self._servo_auto()
                return

            self._log_info("Unloading filament...")
            self._display_visual_state()

            # Check for cases where we must form tip
            park_pos = 0.
            if not skip_tip and self.loaded_status >= self.LOADED_STATUS_PARTIAL_IN_EXTRUDER:
                detected, park_pos = self._form_tip_standalone()
                if detected:
                    # Definitely in extruder
                    self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_EXTRUDER)
                else:
                    # No movement means we can safely assume we are somewhere in the bowden
                    self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_BOWDEN)

# PAUL don't think this is necessary ..
#            if self.loaded_status == self.LOADED_STATUS_PARTIAL_END_OF_BOWDEN and self._has_toolhead_sensor():
#                # This error case can occur when home to sensor failed and we may be stuck in extruder
#                # But can also occur on full load without homing to extruder (this case the unload_extruder is redundant)
#                self._unload_extruder(park_pos=park_pos)
#                self._unload_encoder(length) # Full slow unload
#                self._set_gate_status(self.gate_selected, self.GATE_AVAILABLE_FROM_BUFFER)
#
            if self.loaded_status >= self.LOADED_STATUS_PARTIAL_HOMED_SENSOR:
                # Exit extruder, fast unload of bowden, then slow unload encoder
                self._unload_extruder(park_pos=park_pos)
                self._unload_bowden(length - self.unload_buffer, skip_sync_move=skip_sync_move)
                self._unload_encoder(self.unload_buffer)
                self._set_gate_status(self.gate_selected, self.GATE_AVAILABLE_FROM_BUFFER)

            elif self.loaded_status >= self.LOADED_STATUS_PARTIAL_HOMED_EXTRUDER:
                # fast unload of bowden, then slow unload encoder
                self._unload_bowden(length - self.unload_buffer, skip_sync_move=skip_sync_move)
                self._unload_encoder(self.unload_buffer)
                self._set_gate_status(self.gate_selected, self.GATE_AVAILABLE_FROM_BUFFER)

            elif self.loaded_status >= self.LOADED_STATUS_PARTIAL_END_OF_BOWDEN:
                # Have to do slow unload because we don't know exactly where at the end of the bowden we are
                self._unload_encoder(length) # Full slow unload
                self._set_gate_status(self.gate_selected, self.GATE_AVAILABLE_FROM_BUFFER)

            elif self.loaded_status >= self.LOADED_STATUS_PARTIAL_BEFORE_ENCODER:
                # Have to do slow unload because we don't know exactly where we are
                self._unload_encoder(length) # Full slow unload

            else:
                self._log_debug("Assertion failure - unexpected state %d in _unload_sequence()" % self.loaded_status)
                raise MmuError("Unexpected state during unload sequence")

            movement = self._servo_up()
            if movement > self.encoder_min:
                raise MmuError("It may be time to get the pliers out! Filament appears to stuck somewhere")

            self.toolhead.wait_moves()
            self._log_info("Unloaded %.1fmm of filament" % self.encoder_sensor.get_distance())
            self.encoder_sensor.reset_counts()    # Encoder 0000

        except MmuError as ee:
            self._track_gate_statistics('unload_failures', self.gate_selected)
            raise MmuError(ee)

        finally:
            self._track_unload_end()
            self._set_action(current_action)

    # This is a recovery routine to determine the most conservative location of the filament for unload purposes
    def _recover_loaded_state(self):
        toolhead_sensor_state = self._check_toolhead_sensor()
        if toolhead_sensor_state == -1:     # Not installed
            if self._check_filament_in_encoder():
                # Definitely now just in extruder
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_EXTRUDER)
            else:
                # No movement means we can safely assume we are somewhere in the bowden
                self._set_loaded_status(self.LOADED_STATUS_UNLOADED)
        elif toolhead_sensor_state == 1:    # Filament detected in toolhead
            self._set_loaded_status(self.LOADED_STATUS_FULL)
        else:                               # Filament not detected in toolhead
            if self._check_filament_in_encoder():
                if self._check_filament_still_in_extruder():
                    self._set_loaded_status(self.LOADED_STATUS_PARTIAL_HOMED_EXTRUDER) # We can do a fast unload
                else:
                    self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_BOWDEN) # This prevents fast unload move
            else:
                self._set_loaded_status(self.LOADED_STATUS_UNLOADED)

    # Step 1 of the unload sequence
    # Extract filament past extruder gear (to end of bowden)
    # Assume that tip has already been formed and we are parked somewhere in the encoder either by
    # slicer or by stand alone tip creation
    def _unload_extruder(self, extruder_stepper_only=False, park_pos=0.):
        current_action = self._set_action(self.ACTION_UNLOADING_EXTRUDER)
        try:
            self._log_debug("Extracting filament from extruder")
            self.filament_direction = self.DIRECTION_UNLOAD
            self._set_above_min_temp()
            sync_allowed = self.sync_unload_extruder and not extruder_stepper_only
            if sync_allowed:
                self._servo_down()
            else:
                self._servo_up()

            # Goal is to exit extruder. Strategies depend on availability of toolhead sensor and synced motor option
            out_of_extruder = False
            safety_margin = 5.

            if self._has_toolhead_sensor():
                # This strategy supports both extruder only and 'synced' modes of operation
                # Home to toolhead sensor, then move the remained fixed distance
                motor = "gear+extruder" if sync_allowed else "extruder"
                speed = self.extruder_sync_unload_speed if sync_allowed else self.extruder_unload_speed
                length = self._get_home_position_to_nozzle() - park_pos + safety_margin
                homed, actual, delta = self._trace_filament_move("Reverse homing to toolhead sensor",
                        -length, motor=motor, homing_move=-1, endstop="mmu_toolhead")
                if homed:
                    # Last move to ensure we are really free because of small space between sensor and extruder gears
                    length += actual # Actual is -ve
                    if self.sensor_to_nozzle > 0. and self.extruder_to_nozzle > 0.:
                        length = self.extruder_to_nozzle - self.sensor_to_nozzle + safety_margin
                    delta = self._trace_filament_move("Move from toolhead sensor to exit",
                            -length, speed=speed, motor=motor)
                    out_of_extruder = True

            elif not sync_allowed:
                # No toolhead sensor and not syncing gear and extruder motors:
                # Back up around 15mm at a time until either the encoder doesn't see any movement
                # Do this until we have traveled more than the length of the extruder
                step = self.encoder_move_step_size
                length = self._get_home_position_to_nozzle() - park_pos + step + safety_margin
                speed = self.extruder_unload_speed * 0.5 # First pull slower just in case we don't have tip
                self._log_debug("Trying to exit the extruder, up to %.1fmm in %.1fmm steps" % (length, step))
                for i in range(int(math.ceil(length / step))):
                    msg = "Step #%d:" % (i+1)
                    delta = self._trace_filament_move(msg, -step, speed=speed, motor="extruder")
                    speed = self.extruder_unload_speed  # Can pull at full speed on subsequent steps

                    if (step - delta) < self.encoder_min:
                        self._log_debug("Extruder entrance reached after %d moves" % (i+1))
                        out_of_extruder = True
                        break

            else:
                # No toolhead sensor with synced steppers:
                # This is a challenge because we don't know the filament "park position" in extruder
                # If we go too far, bowden unload may accidentally unload encoder
                # Simply move the maximum distance in steps to sanity check we are moving
                step = self.encoder_move_step_size
                length = self._get_home_position_to_nozzle() - park_pos + safety_margin
                speed = self.extruder_unload_speed * 0.5 # First pull slower just in case we don't have tip
                self._log_debug("Trying to exit the extruder, up to %.1fmm in %.1fmm steps" % (length, step))
                stuck_in_extruder = False
                for i in range(int(math.ceil(length / step))):
                    msg = "Step #%d:" % (i+1)
                    delta = self._trace_filament_move(msg, -step, speed=speed, motor="gear+extruder")
                    speed = self.extruder_unload_speed  # Can pull at full speed on subsequent steps

                    if (step - delta) < self.encoder_min:
                        self._log_debug("No encoder movement despite both steppers are pulling after %d moves" % (i+1))
                        stuck_in_extruder = True
                        break
                if not stuck_in_extruder:
                    out_of_extruder = True
# PAUL TODO This causes unecessary servo movement and is probably unecessary
#                    # Back up just a bit with only the extruder, if we don't see any movement.
#                    # then the filament is out of the extruder
#                    out_of_extruder = self._test_filament_in_extruder_by_retracting()
#                    if out_of_extruder:
#                        self._log_debug("Extruder entrance reached after %d moves" % (i+1))

            if not out_of_extruder:
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_EXTRUDER)
                raise MmuError("Filament seems to be stuck in the extruder")

            self._log_debug("Filament should be out of extruder")
            self._set_loaded_status(self.LOADED_STATUS_PARTIAL_END_OF_BOWDEN)

        finally:
            self._set_action(current_action)

    # Retract the filament by the extruder stepper only and see if we do not have any encoder movement
    # This assumes that we already tip formed, and the filament is parked somewhere in the extruder
    # Return True if filament is out of extruder
    def _test_filament_in_extruder_by_retracting(self, length=None):
        self._log_debug("Testing for filament in extruder by retracting")
        if not length:
            length = self.encoder_move_step_size
        self._servo_up()
        delta = self._trace_filament_move("Moving extruder to test for exit",
                -length, speed=self.extruder_unload_speed, motor="extruder")
        return (length - delta) < self.encoder_min

    # Step 2 of the unload sequence
    # Fast unload of filament from exit of extruder gear (end of bowden) to position close to MMU (but still in encoder)
    def _unload_bowden(self, length, skip_sync_move=False):
        self._log_debug("Unloading bowden tube")
        self.filament_direction = self.DIRECTION_UNLOAD
        tolerance = self.unload_bowden_tolerance
        self._servo_down()

# PAUL TODO Don't like this test move anymore. Complicates things and servo is now more reliable
#        # Initial short move allows for dealing with (servo) errors.
#        if not self.calibrating:
#            sync = not skip_sync_move and self.transition_length > 0
#            initial_move = 10. if not sync else self.transition_length
#            if sync:
#                self._log_debug("Moving the gear and extruder motors in sync for %.1fmm" % -initial_move)
#                delta = self._trace_filament_move("Sync unload", -initial_move, speed=self.extruder_sync_unload_speed, motor="both")
#            else:
#                self._log_debug("Moving the gear motor for %.1fmm" % -initial_move)
#                delta = self._trace_filament_move("Unload", -initial_move, speed=self.extruder_sync_unload_speed, motor="gear", track=True)
#
#            if delta > max(initial_move * 0.5, 1): # 50% slippage
#                self._log_always("Error unloading filament - not enough detected at encoder. Suspect servo not properly down. Retrying...")
#                self._track_gate_statistics('servo_retries', self.gate_selected)
#                self._servo_up()
#                self._servo_down()
#                if sync:
#                    delta = self._trace_filament_move("Retrying sync unload move after servo reset", -delta, speed=self.extruder_sync_unload_speed, motor="both")
#                else:
#                    delta = self._trace_filament_move("Retrying unload move after servo reset", -delta, speed=self.extruder_sync_unload_speed, motor="gear", track=True)
#                if delta > max(initial_move * 0.5, 1): # 50% slippage
#                    # Actually we are likely still stuck in extruder
#                    self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_EXTRUDER)
#                    raise MmuError("Too much slippage (%.1fmm) detected during the sync unload from extruder. Maybe still stuck in extruder" % delta)
#            length -= (initial_move - delta)
#        # Continue fast unload

        moves = 1 if length < (self.calibrated_bowden_length / self.num_moves) else self.num_moves
        delta = 0
        for i in range(moves):
            msg = "Course unloading move #%d from bowden" % (i+1)
            delta += self._trace_filament_move(msg, -length / moves, track=True)
            if i < moves:
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_IN_BOWDEN)
        if delta >= length * 0.8 and not self.calibrating: # 80% slippage detects filament still stuck in extruder
            raise MmuError("Failure to unload bowden. Perhaps filament is stuck in extruder. Gear moved %.1fmm, Encoder delta %.1fmm" % (length, delta))
        elif delta >= tolerance and not self.calibrating:
            # Only a warning because _unload_encoder() will deal with it
            self._log_info("Warning: Excess slippage was detected in bowden tube unload. Gear moved %.1fmm, Encoder delta %.1fmm" % (length, delta))
        self._set_loaded_status(self.LOADED_STATUS_PARTIAL_PAST_ENCODER)

    # Step 3 of the unload sequence
    # Slow (step) extract of filament from encoder to MMU park position
    def _unload_encoder(self, max_length):
        self._log_debug("Slow unload of the encoder")
        self.filament_direction = self.DIRECTION_UNLOAD
        max_steps = int(max_length / self.encoder_move_step_size) + 5
        self._servo_down()
        for i in range(max_steps):
            msg = "Unloading step #%d from encoder" % (i+1)
            delta = self._trace_filament_move(msg, -self.encoder_move_step_size)
            # Large enough delta here means we are out of the encoder
            if delta >= self.encoder_move_step_size * 0.2: # 20 %
                self._set_loaded_status(self.LOADED_STATUS_PARTIAL_BEFORE_ENCODER)
                park = self.parking_distance - delta # will be between 8 and 20mm (for 23mm parking_distance, 15mm step)
                delta = self._trace_filament_move("Final parking", -park)
                # We don't expect any movement of the encoder unless it is free-spinning
                if park - delta > self.encoder_min: # We expect 0, but relax the test a little (allow one pulse)
                    self._log_info("Warning: Possible encoder malfunction (free-spinning) during final filament parking")
                self._set_loaded_status(self.LOADED_STATUS_UNLOADED)
                return
        raise MmuError("Unable to get the filament out of the encoder cart")

    # Form tip and return True if filament detected and the filament park position
    def _form_tip_standalone(self, extruder_stepper_only=False):
        self.toolhead.wait_moves()
        filament_present = self._check_toolhead_sensor()
        if filament_present == 0:
            self._log_debug("Tip forming skipped because no filament was detected")
            return False, 0.

        current_action = self._set_action(self.ACTION_FORMING_TIP)
        try:
            self._log_info("Forming tip...")
            self._set_above_min_temp()
            self._sync_gear_to_extruder(self.sync_form_tip and not extruder_stepper_only, servo=True)

            if self.extruder_tmc and self.extruder_form_tip_current > 100:
                extruder_run_current = self.extruder_tmc.get_status(0)['run_current']
                self._log_debug("Temporarily increasing extruder run current to %d%% for tip forming move"
                                    % self.extruder_form_tip_current)
                self.gcode.run_script_from_command("SET_TMC_CURRENT STEPPER=%s CURRENT=%.2f"
                                                    % (self.extruder_name, (extruder_run_current * self.extruder_form_tip_current)/100.))

            initial_extruder_position = self.mmu_extruder_stepper.stepper.get_commanded_position()
            initial_encoder_position = self.encoder_sensor.get_distance()
            initial_pa = self.printer.lookup_object(self.extruder_name).get_status(0)['pressure_advance'] # Capture PA in case user's tip forming resets it
            self.gcode.run_script_from_command("_MMU_FORM_TIP_STANDALONE")
            self.gcode.run_script_from_command("SET_PRESSURE_ADVANCE ADVANCE=%.4f" % initial_pa) # Restore PA
            self.toolhead.wait_moves()
            delta = self.encoder_sensor.get_distance() - initial_encoder_position
            park_pos = initial_extruder_position - self.mmu_extruder_stepper.stepper.get_commanded_position()
            self._log_trace("After tip formation, extruder moved: %.2f, encoder moved %.2f" % (park_pos ,delta))
            self.encoder_sensor.set_distance(initial_encoder_position + park_pos)

            if self.extruder_tmc and self.extruder_form_tip_current > 100:
                self.gcode.run_script_from_command("SET_TMC_CURRENT STEPPER=%s CURRENT=%.2f" % (self.extruder_name, extruder_run_current))

            if self.sync_form_tip and not extruder_stepper_only:
                if delta > self.encoder_min:
                    if filament_present == 1:
                        return True, park_pos
                    else:
                        if self.gear_stepper.is_synced():
                            # If we are currently synced just assume filament is found because
                            # the extruder test is disruptive if servo is already down
                            return True, park_pos
                        else:
                            out_of_extruder = self._test_filament_in_extruder_by_retracting()
                            return not out_of_extruder, park_pos
                else:
                    if filament_present == 1:
                        # Big clog!
                        raise MmuError("Filament stuck in extruder")
                    else:
                        # Either big clog or more likely, no toolhead sensor and no filament in MMU
                        return False, park_pos
            else:
                # Not synchronous gear/extruder movement
                return (delta > self.encoder_min or filament_present == 1), park_pos
        finally:
            self._sync_gear_to_extruder(False)
            self._set_action(current_action)


#################################################
# TOOL SELECTION AND SELECTOR CONTROL FUNCTIONS #
#################################################

    def _home(self, tool = -1, force_unload = -1):
        if self._check_in_bypass(): return
        current_action = self._set_action(self.ACTION_HOMING)
        try:
            self._log_info("Homing MMU...")
            if self.is_paused_locked:
                self._log_debug("MMU is locked, unlocking it before continuing...")
                self._unlock()

            if force_unload != -1:
                self._log_debug("(asked to %s)" % ("force unload" if force_unload == 1 else "not unload"))
            if force_unload == 1:
                # Forced unload case for recovery
                self._unload_sequence(self.calibrated_bowden_length, check_state=True)
            elif force_unload == -1 and self.loaded_status != self.LOADED_STATUS_UNLOADED:
                # Automatic unload case
                self._unload_sequence(self.calibrated_bowden_length)

            self._unselect_tool()
            self._home_selector()
            if tool >= 0:
                self._select_tool(tool)
        finally:
            self._set_action(current_action)

    def _home_selector(self):
        self.is_homed = False
        self.gate_selected = self.TOOL_UNKNOWN
        self._servo_move()
        num_channels = self.num_gates
        selector_length = 10. + (num_channels-1)*21. + ((num_channels-1)//3)*5. + (self.bypass_offset > 0)
        self._log_debug("Moving up to %.1fmm to home a %d channel MMU" % (selector_length, num_channels))
        self.toolhead.wait_moves()
        try:
            self.selector_stepper.do_set_position(0.)
            self._selector_stepper_move_wait(-selector_length, speed=self.selector_homing_speed, homing_move=1) # Fast homing move
            self.selector_stepper.do_set_position(0.)
            self._selector_stepper_move_wait(5, False)                    # Ensure some bump space
            self._selector_stepper_move_wait(-5, speed=10, homing_move=1) # Slower more accurate homing move
            self.is_homed = True
        except Exception as e:
            # Homing failed
            self._set_tool_selected(self.TOOL_UNKNOWN)
            raise MmuError("Homing selector failed because of blockage or malfunction. Klipper reports: %s" % str(e))
        self.selector_stepper.do_set_position(0.)

    def _move_selector_touch(self, target):
        successful, travel = self._attempt_selector_move(target)
        if not successful:
            if abs(travel) < 3.0:       # Filament stuck in the current selector
                self._log_info("Selector is blocked by inside filament, trying to recover...")
                # Realign selector
                self.selector_stepper.do_set_position(0.)
                self._log_trace("Resetting selector by a distance of: %.1fmm" % -travel)
                self._selector_stepper_move_wait(-travel)

                # See if we can detect filament in the encoder
                self._servo_down()
                found = self._buzz_gear_motor()
                if not found:
                    # Try to engage filament to the encoder
                    delta = self._trace_filament_move("Trying to re-enguage encoder", 45.)
                    if delta == 45.:    # No movement
                        # Could not reach encoder
                        raise MmuError("Selector recovery failed. Path is probably internally blocked and unable to move filament to clear")

                # Now try a full unload sequence
                try:
                    self._unload_sequence(self.calibrated_bowden_length, check_state=True)
                except MmuError as ee:
                    # Add some more context to the error and re-raise
                    raise MmuError("Selector recovery failed because: %s" % (str(ee)))

                # Ok, now check if selector can now reach proper target
                self._home_selector()
                successful, travel = self._attempt_selector_move(target)
                if not successful:
                    # Selector path is still blocked
                    self.is_homed = False
                    self._unselect_tool()
                    raise MmuError("Selector recovery failed. Path is probably internally blocked")
            else :                          # Selector path is blocked, probably not internally
                self.is_homed = False
                self._unselect_tool()
                raise MmuError("Selector path is probably externally blocked")

    def _attempt_selector_move(self, target):
        selector_steps = self.selector_stepper.stepper.get_step_dist()
        init_position = self.selector_stepper.get_position()[0]
        init_mcu_pos = self.selector_stepper.stepper.get_mcu_position()
        target_move = target - init_position
        self._selector_stepper_move_wait(target, speed=self.selector_touch_speed, homing_move=2, endstop=self.SELECTOR_TOUCH_ENDSTOP)
        mcu_position = self.selector_stepper.stepper.get_mcu_position()
        travel = (mcu_position - init_mcu_pos) * selector_steps
        delta = abs(target_move - travel)
        self._log_trace("Selector moved %.1fmm of intended travel from: %.1fmm to: %.1fmm (delta: %.1fmm)"
                        % (travel, init_position, target, delta))
        if delta <= 1.0 :
            # True up position
            self._log_trace("Truing selector %.1fmm to %.1fmm" % (delta, target))
            self.selector_stepper.do_set_position(init_position + travel)
            self._selector_stepper_move_wait(target)
            return True, travel
        else:
            return False, travel

    # This is the main function for initiating a tool change, it will handle unload if necessary
    def _change_tool(self, tool, skip_tip=True):
        self._log_debug("Tool change initiated %s" % ("with slicer forming tip" if skip_tip else "with standalone MMU tip formation"))
        self._sync_gear_to_extruder(False)
        skip_unload = False
        initial_tool_string = "Unknown" if self.tool_selected < 0 else ("T%d" % self.tool_selected)
        if tool == self.tool_selected and self.tool_to_gate_map[tool] == self.gate_selected and self.loaded_status == self.LOADED_STATUS_FULL:
            self._log_always("Tool T%d is already ready" % tool)
            return

        if self.loaded_status == self.LOADED_STATUS_UNLOADED:
            skip_unload = True
            msg = "Tool change requested: T%d" % tool
            m117_msg = ("> T%d" % tool)
        else:
            msg = "Tool change requested, from %s to T%d" % (initial_tool_string, tool)
            m117_msg = ("%s > T%d" % (initial_tool_string, tool))
        # Important to always inform user in case there is an error and manual recovery is necessary
        self._last_toolchange = m117_msg
        self.gcode.run_script_from_command("M117 %s" % m117_msg)
        self._log_always(msg)

        # Check TTG map. We might be mapped to same gate
        if self.tool_to_gate_map[tool] == self.gate_selected and self.loaded_status == self.LOADED_STATUS_FULL:
            self._select_tool(tool)
            self.gcode.run_script_from_command("M117 T%s" % tool)
            return

        # Identify the unitialized startup use case and make it easy for user
        if not self.is_homed and self.tool_selected == self.TOOL_UNKNOWN:
            self._log_info("MMU not homed, homing it before continuing...")
            self._home(tool)
            skip_unload = True

        if not skip_unload:
            self._unload_tool(skip_tip=skip_tip)
        self._select_and_load_tool(tool)
        self._track_swap_completed()
        self.gcode.run_script_from_command("M117 T%s" % tool)

    def _unselect_tool(self):
        self._set_tool_selected(self.TOOL_UNKNOWN)
        self._servo_auto()

    def _select_tool(self, tool, move_servo=True):
        if tool < 0 or tool >= self.num_gates:
            self._log_always("Tool %d does not exist" % tool)
            return

        gate = self.tool_to_gate_map[tool]
        self._log_info("PAUL: tool=%d, selected_tool=%s, selected_gate=%s" % (tool, self.tool_selected, self.gate_selected))
# PAUL TODO this allows for correction of servo position if incorrect. Do we need it?
#        if tool == self.tool_selected and gate == self.gate_selected \
#                and (self.servo_state == self.SERVO_UP_STATE or self.servo_state == self.SERVO_DOWN_STATE):
        if tool == self.tool_selected and gate == self.gate_selected:
            return

        self._log_debug("Selecting tool T%d on gate #%d..." % (tool, gate))
        self._select_gate(gate)
        self._set_tool_selected(tool)
        if move_servo:
            self._servo_auto()
        self._log_info("Tool T%d enabled%s" % (tool, (" on gate #%d" % gate) if tool != gate else ""))

    def _select_gate(self, gate):
        if gate == self.gate_selected: return
        current_action = self._set_action(self.ACTION_SELECTING)
        try:
            self._servo_move()
            if gate == self.TOOL_BYPASS:
                offset = self.bypass_offset
            else:
                offset = self.selector_offsets[gate]
            if self.selector_touch:
                self._move_selector_touch(offset)
            else:
                self._selector_stepper_move_wait(offset)
            self._set_gate_selected(gate)
        finally:
            self._set_action(current_action)

    def _select_bypass(self):
        if self.tool_selected == self.TOOL_BYPASS and self.gate_selected == self.TOOL_BYPASS: return
        if self.bypass_offset == 0:
            self._log_always("Bypass not configured")
            return
        current_action = self._set_action(self.ACTION_SELECTING)
        try:
            self._log_info("Selecting filament bypass...")
            self._select_gate(self.TOOL_BYPASS)
            self.filament_direction = self.DIRECTION_LOAD
            self._set_tool_selected(self.TOOL_BYPASS)
            self._log_info("Bypass enabled")
        finally:
            self._set_action(current_action)

    def _set_gate_selected(self, gate):
        self.gate_selected = gate
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_GATE_SELECTED, self.gate_selected))
        if gate == self.TOOL_UNKNOWN or gate == self.TOOL_BYPASS:
            self._set_gate_ratio(1.)
        else:
            self._set_gate_ratio(self._get_gate_ratio(gate))

    def _set_tool_selected(self, tool):
        self.tool_selected = tool
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_TOOL_SELECTED, self.tool_selected))

    def _set_gate_ratio(self, ratio=1.):
        self._log_trace("Setting MMU gear motor rotation distance ratio to %.6f" % ratio)
        new_rotation_distance = ratio * self.ref_gear_rotation_distance
        self.gear_stepper.stepper.set_rotation_distance(new_rotation_distance)


### CORE GCODE COMMANDS ##########################################################

    cmd_MMU_UNLOCK_help = "Unlock MMU operations after an error condition"
    def cmd_MMU_UNLOCK(self, gcmd):
        if self._check_is_disabled(): return
        if not self.is_paused_locked:
            self._log_info("MMU is not locked")
            return
        self._log_info("Unlocking the MMU")
        self._unlock()
        self._log_info("When the issue is addressed you can resume print")

    cmd_MMU_HOME_help = "Home the MMU selector"
    def cmd_MMU_HOME(self, gcmd):
        if self._check_is_disabled(): return
        tool = gcmd.get_int('TOOL', 0, minval=0, maxval=self.num_gates - 1)
        force_unload = gcmd.get_int('FORCE_UNLOAD', -1, minval=0, maxval=1)
        try:
            self._home(tool, force_unload)
        except MmuError as ee:
            self._pause(str(ee))

    cmd_MMU_SELECT_help = "Select the specified logical tool (following TTG map) or physical gate"
    def cmd_MMU_SELECT(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_not_homed(): return
        if self._check_is_loaded(): return
        tool = gcmd.get_int('TOOL', -1, minval=0, maxval=self.num_gates - 1)
        gate = gcmd.get_int('GATE', -1, minval=0, maxval=self.num_gates - 1)
        if tool == -1 and gate == -1:
            raise gcmd.error("Error on 'MMU_SELECT': missing TOOL or GATE")
        try:
            if tool != -1:
                self._select_tool(tool)
            else:
                self._select_gate(gate)
                # Find the first tool that maps to this gate or current tool if it maps
                # (Remember multiple tools can map to the same gate)
                if self.tool_selected >= 0 and self.tool_to_gate_map[self.tool_selected] == gate:
                    pass
                else:
                    tool_found = False
                    for tool in range(len(self.tool_to_gate_map)):
                        if self.tool_to_gate_map[tool] == gate:
                            self._select_tool(tool)
                            tool_found = True
                            break
                    if not tool_found:
                        self._set_tool_selected(self.TOOL_UNKNOWN)
        except MmuError as ee:
            self._pause(str(ee))
        finally:
            self._servo_auto()

    cmd_MMU_CHANGE_TOOL_help = "Perform a tool swap"
    def cmd_MMU_CHANGE_TOOL(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        if self._check_is_calibrated(): return
        quiet = gcmd.get_int('QUIET', 0, minval=0, maxval=1)
        tool = gcmd.get_int('TOOL', minval=0, maxval=self.num_gates - 1)
        standalone = bool(gcmd.get_int('STANDALONE', 0, minval=0, maxval=1))
        skip_tip = self._is_in_print() and not standalone
        if self.loaded_status == self.LOADED_STATUS_UNKNOWN and self.is_homed: # Will be done later if not homed
            self._log_error("Unknown filament position, recovering state...")
            self._recover_loaded_state()
        try:
            restore_encoder = self._disable_encoder_sensor(update_clog_detection_length=True) # Don't want runout accidently triggering during tool change
            self._last_tool = self.tool_selected
            self._next_tool = tool
            self._change_tool(tool, skip_tip)
            self._dump_statistics(quiet=quiet)
            self._enable_encoder_sensor(restore_encoder)
        except MmuError as ee:
            self._pause("%s.\nOccured when changing tool: %s" % (str(ee), self._last_toolchange))
        finally:
            self._next_tool = self.TOOL_UNKNOWN

    cmd_MMU_LOAD_help = "Loads filament on current tool/gate or optionally loads just the extruder for bypass or recovery usage (EXTUDER_ONLY=1)"
    def cmd_MMU_LOAD(self, gcmd, bypass=False):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        extruder_only = bool(gcmd.get_int('EXTRUDER_ONLY', 0, minval=0, maxval=1) or bypass)
        restore_encoder = self._disable_encoder_sensor() # Don't want runout accidently triggering during filament load
        try:
            if self.tool_selected != self.TOOL_BYPASS and not extruder_only:
                self._select_and_load_tool(self.tool_selected) # This could change gate tool is mapped to
            elif self.loaded_status != self.LOADED_STATUS_FULL or extruder_only:
                self._load_extruder(True)
            else:
                self._log_always("Filament already loaded")
        except MmuError as ee:
            self._pause(str(ee))
            if self.tool_selected == self.TOOL_BYPASS:
                self._set_loaded_status(self.LOADED_STATUS_UNKNOWN)
        finally:
            self._enable_encoder_sensor(restore_encoder)

    cmd_MMU_EJECT_help = "Eject filament and park it in the MMU or optionally unloads just the extruder (EXTRUDER_ONLY=1)"
    def cmd_MMU_EJECT(self, gcmd, bypass=False):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_is_calibrated(): return
        extruder_only = bool(gcmd.get_int('EXTRUDER_ONLY', 0, minval=0, maxval=1) or bypass)
        restore_encoder = self._disable_encoder_sensor() # Don't want runout accidently triggering during filament unload
        try:
            if self.tool_selected != self.TOOL_BYPASS and not extruder_only:
                self._unload_tool()
            elif self.loaded_status != self.LOADED_STATUS_UNLOADED or extruder_only:
                detected, park_pos = self._form_tip_standalone(extruder_stepper_only=True)
                if detected:
                    self._unload_extruder(extruder_stepper_only=True, park_pos=park_pos)
                if self.tool_selected == self.TOOL_BYPASS:
                    self._set_loaded_status(self.LOADED_STATUS_UNLOADED)
                    self._log_always("Please pull the filament out clear of the MMU selector")
                else:
                    self._set_loaded_status(self.LOADED_STATUS_PARTIAL_HOMED_EXTRUDER)
            else:
                self._log_always("Filament not loaded")
        except MmuError as ee:
            self._pause(str(ee))
        finally:
            self._enable_encoder_sensor(restore_encoder)

    cmd_MMU_SELECT_BYPASS_help = "Select the filament bypass"
    def cmd_MMU_SELECT_BYPASS(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_not_homed(): return
        if self._check_is_loaded(): return
        try:
            self._select_bypass()
        except MmuError as ee:
            self._pause(str(ee))

    cmd_MMU_PAUSE_help = "Pause the current print and lock the MMU operations"
    def cmd_MMU_PAUSE(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        force_in_print = bool(gcmd.get_int('FORCE_IN_PRINT', 0, minval=0, maxval=1))
        self._pause("Pause macro was directly called", force_in_print)

    # Not a user facing command - used in automatic wrapper
    cmd_MMU_RESUME_help = "Wrapper around default RESUME macro"
    def cmd_MMU_RESUME(self, gcmd):
        if not self.is_enabled:
            self.gcode.run_script_from_command("__RESUME") # User defined or Klipper default
            return
        self._log_debug("MMU_RESUME wrapper called")
        if self.is_paused_locked:
            self._log_always("You can't resume the print without unlocking the MMU first")
            return
        if not self.printer.lookup_object("pause_resume").is_paused:
            self._log_always("Print is not paused")
            return

        # Sanity check we are ready to go
        if self._is_in_print() and self.loaded_status != self.LOADED_STATUS_FULL:
            if self._check_toolhead_sensor() == 1:
                self._set_loaded_status(self.LOADED_STATUS_FULL, silent=True)
                self._log_always("Automatically set filament state to LOADED based on toolhead sensor")
            else:
                self._log_always("State does not indicate flament is LOADED.  Please run `MMU_RECOVER LOADED=1` first")
                return

        self._set_above_min_temp(self.paused_extruder_temp)
        self.gcode.run_script_from_command("__RESUME")
        self._restore_toolhead_position()
        self.encoder_sensor.reset_counts()    # Encoder 0000
        self._enable_encoder_sensor(True)
        if self.sync_to_extruder:
            self._sync_gear_to_extruder(True, servo=True, in_print=True)
        # Continue printing...

    # Not a user facing command - used in automatic wrapper
    cmd_MMU_CANCEL_PRINT_help = "Wrapper around default CANCEL_PRINT macro"
    def cmd_MMU_CANCEL_PRINT(self, gcmd):
        if not self.is_enabled:
            self.gcode.run_script_from_command("__CANCEL_PRINT") # User defined or Klipper default
            return
        self._log_debug("MMU_CANCEL_PRINT wrapper called")
        if self.is_paused_locked:
            self._track_pause_end()
            self.is_paused_locked = False
        self.reactor.update_timer(self.heater_off_handler, self.reactor.NEVER)
        self._save_toolhead_position_and_lift(False)
        self.gcode.run_script_from_command("__CANCEL_PRINT")

    cmd_MMU_RECOVER_help = "Recover the filament location and set MMU state after manual intervention/movement"
    def cmd_MMU_RECOVER(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        tool = gcmd.get_int('TOOL', self.TOOL_UNKNOWN, minval=-2, maxval=self.num_gates - 1)
        mod_gate = gcmd.get_int('GATE', self.TOOL_UNKNOWN, minval=-2, maxval=self.num_gates - 1)
        loaded = gcmd.get_int('LOADED', -1, minval=0, maxval=1)

        if (tool == self.TOOL_BYPASS or mod_gate == self.TOOL_BYPASS) and self.bypass_offset == 0:
            self._log_always("Bypass not configured")
            return

        if tool == self.TOOL_BYPASS:
            self._set_gate_selected(self.TOOL_BYPASS)
            self._set_tool_selected(self.TOOL_BYPASS)
        elif tool >= 0: # If tool is specified then use and optionally override the gate
            self._set_tool_selected(tool)
            gate = self.tool_to_gate_map[tool]
            if mod_gate >= 0:
                gate = mod_gate
            if gate >= 0:
                self._remap_tool(tool, gate, loaded)
                self._set_gate_selected(gate)
        elif tool == self.TOOL_UNKNOWN and self.tool_selected == self.TOOL_BYPASS and loaded == -1:
            # This is to be able to get out of "stuck in bypass" state
            self._log_info("Warning: Making assumption that bypass is unloaded")
            self.filament_direction = self.DIRECTION_LOAD
            self._set_loaded_status(self.LOADED_STATUS_UNLOADED, silent=True)
            self._servo_auto()
            return

        if loaded == 1:
            self.filament_direction = self.DIRECTION_LOAD
            self._set_loaded_status(self.LOADED_STATUS_FULL)
            return
        elif loaded == 0:
            self.filament_direction = self.DIRECTION_LOAD
            self._set_loaded_status(self.LOADED_STATUS_UNLOADED)
            return

        # Filament position not specified so auto recover
        self._log_info("Recovering filament position/state...")
        self._recover_loaded_state()
        self._servo_auto()


### GCODE COMMANDS INTENDED FOR TESTING #####################################

    cmd_MMU_SOAKTEST_SELECTOR_help = "Soak test of selector movement"
    def cmd_MMU_SOAKTEST_SELECTOR(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_is_loaded(): return
        if self._check_is_calibrated(self.CALIBRATED_SELECTOR): return
        loops = gcmd.get_int('LOOP', 100)
        servo = bool(gcmd.get_int('SERVO', 0))
        try:
            self._home()
            for l in range(loops):
                self._log_always("Testing loop %d / %d" % (l + 1, loops))
                tool = randint(0, self.num_gates)
                if tool == self.num_gates:
                    self._select_bypass()
                else:
                    if randint(0, 10) == 0:
                        self._home(tool)
                    else:
                        self._select_tool(tool, move_servo=servo)
                if servo:
                    self._servo_down()
        except MmuError as ee:
            self._log_error("Soaktest abandoned because of error")
            self._log_always(str(ee))

    cmd_MMU_SOAKTEST_LOAD_SEQUENCE_help = "Soak test tool load/unload sequence"
    def cmd_MMU_SOAKTEST_LOAD_SEQUENCE(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        if self._check_not_homed(): return
        if self._check_is_calibrated(): return
        loops = gcmd.get_int('LOOP', 10)
        random = gcmd.get_int('RANDOM', 0)
        to_nozzle = gcmd.get_int('FULL', 0)
        try:
            for l in range(loops):
                self._log_always("Testing loop %d / %d" % (l, loops))
                for t in range(self.num_gates):
                    tool = t
                    if random == 1:
                        tool = randint(0, self.num_gates - 1)
                    gate = self.tool_to_gate_map[tool]
                    if self.gate_status[gate] == self.GATE_EMPTY:
                        self._log_always("Skipping tool %d of %d because gate %d is empty" % (tool, self.num_gates, gate))
                    else:
                        self._log_always("Testing tool %d of %d (gate %d)" % (tool, self.num_gates, gate))
                        if not to_nozzle:
                            self._select_tool(tool)
                            self._load_sequence(100, no_extruder=True)
                            self._unload_sequence(100, skip_sync_move=True)
                        else:
                            self._select_and_load_tool(tool)
                            self._unload_tool()
            self._select_tool(0)
        except MmuError as ee:
            self._pause(str(ee))

    cmd_MMU_TEST_GRIP_help = "Test the MMU grip for a Tool"
    def cmd_MMU_TEST_GRIP(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        self._servo_down()
        self._motors_off(motor="gear")

    cmd_MMU_TEST_TRACKING_help = "Test the tracking of gear feed and encoder sensing"
    def cmd_MMU_TEST_TRACKING(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        if self._check_not_homed(): return
        if self._check_is_calibrated(): return
        direction = gcmd.get_int('DIRECTION', 1, minval=-1, maxval=1)
        step = gcmd.get_float('STEP', 1, minval=0.5, maxval=20)
        sensitivity = gcmd.get_float('SENSITIVITY', self.encoder_min_resolution, minval=0.1, maxval=10)
        if direction == 0: return
        try:
            if not self._is_filament_in_bowden():
                # Ready MMU for test if not already setup
                self._unload_tool()
                self._load_sequence(100 if direction == 1 else 200, no_extruder=True)
            self.encoder_sensor.reset_counts()    # Encoder 0000
            for i in range(1, int(100 / step)):
                delta = self._trace_filament_move("Test move", direction * step)
                measured = self.encoder_sensor.get_distance()
                moved = i * step
                drift = int(round((moved - measured) / sensitivity))
                if drift > 0:
                    drift_str = "++++++++!!"[0:drift]
                elif (moved - measured) < 0:
                    drift_str = "--------!!"[0:-drift]
                else:
                    drift_str = ""
                self._log_info("Gear/Encoder : %05.2f / %05.2f mm %s" % (moved, measured, drift_str))
            self._unload_tool()
        except MmuError as ee:
            self._log_always("Tracking test failed: %s" % str(ee))

    cmd_MMU_TEST_LOAD_help = "For quick testing filament loading from gate to the extruder"
    def cmd_MMU_TEST_LOAD(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        if self._check_is_loaded(): return
        full = gcmd.get_int('FULL', 0, minval=0, maxval=1)
        if full:
            length = self.calibrated_bowden_length # May cause homing to extruder because full
        else:
            length = gcmd.get_float('LENGTH', 100, minval=10., maxval=self.calibrated_bowden_length)
        try:
            self._load_sequence(length, no_extruder=True)
        except MmuError as ee:
            self._log_always("Load test failed: %s" % str(ee))

    cmd_MMU_TEST_MOVE_help = "Test filament move to help debug setup / options"
    def cmd_MMU_TEST_MOVE(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_is_paused(): return
        if self._check_in_bypass(): return
        length = gcmd.get_float('LENGTH', 100.)
        speed = gcmd.get_float('SPEED', None)
        accel = gcmd.get_float('ACCEL', None)
        motor = gcmd.get('MOTOR', "gear")
        if motor != "gear" and motor != "extruder" and motor != "gear+extruder" and motor != "extruder+gear":
            raise gcmd.error("Valid motor names are 'gear', 'extruder' or 'gear+extruder' or 'extruder+gear'")
        if motor != "extruder":
            self._servo_down()
        else:
            self._servo_up()

        delta = self._trace_filament_move("Test move", length, motor=motor, speed=speed, accel=accel)
        measured_movement = abs(length) - delta
        self._log_always("Moved %.1fmm, Encoder measured %.1fmm)" % (length, measured_movement))

    cmd_MMU_TEST_HOMING_MOVE_help = "Test filament homing move to help debug setup / options"
    def cmd_MMU_TEST_HOMING_MOVE(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_in_bypass(): return
        endstop = gcmd.get('ENDSTOP', "default")
        length = gcmd.get_float('LENGTH', 100.)
        speed = gcmd.get_float('SPEED', None)
        accel = gcmd.get_float('ACCEL', None)
        motor = gcmd.get('MOTOR', "gear")
        if motor != "gear" and motor != "extruder" and motor != "gear+extruder" and motor != "extruder+gear":
            raise gcmd.error("Valid motor names are 'gear', 'extruder' or 'gear+extruder' or 'extruder+gear'")
        direction = -1 if length < 0 else 1
        stop_on_endstop = gcmd.get_int('STOP_ON_ENDSTOP', direction, minval=-1, maxval=1)
        stepper = self.mmu_extruder_stepper if motor.startswith("extruder") else self.gear_stepper
        valid_endstops = list(stepper.get_endstop_names())
        if not endstop in valid_endstops:
            raise gcmd.error("Endstop name '%s' is not valid for motor '%s'. Options are: %s" % (endstop, motor, ', '.join(valid_endstops)))
        if stepper.is_endstop_virtual(endstop) and stop_on_endstop == -1:
            raise gcmd.error("Cannot reverse home on virtual (TMC stallguard) endstop '%s'" % endstop)
        if motor != "extruder":
            self._servo_down()
            if stepper.is_endstop_virtual(endstop):
                self.toolhead.dwell(1) # TMC needs time to settle after gear buzz for servo
        else:
            self._servo_up()

        self._log_debug("Homing '%s' motor to '%s' endstop, up to %.1fmm..." % (motor, endstop, length))
        homed, actual, delta = self._trace_filament_move("Test homing move",
                length, motor=motor, homing_move=stop_on_endstop, speed=speed, accel=accel, endstop=endstop)
        measured_movement = abs(actual) - delta
        self._log_always("%s after %.1fmm (measured %.1fmm)" % (("Homed" if homed else "Did not home"), actual, measured_movement))

    cmd_MMU_TEST_CONFIG_help = "Runtime adjustment of MMU configuration for testing or in-print tweaking purposes"
    def cmd_MMU_TEST_CONFIG(self, gcmd):
        # Filament Speeds
        self.gear_short_move_speed = gcmd.get_float('GEAR_SHORT_MOVES_SPEED', self.gear_short_move_speed, above=10.)
        self.gear_from_buffer_speed = gcmd.get_float('GEAR_FROM_BUFFER_SPEED', self.gear_from_buffer_speed, above=10.)
        self.gear_from_spool_speed = gcmd.get_float('GEAR_FROM_SPOOL_SPEED', self.gear_from_spool_speed, above=10.)
        self.gear_homing_speed = gcmd.get_float('GEAR_HOMING_SPEED', self.gear_homing_speed, above=1.)
        self.extruder_homing_speed = gcmd.get_float('EXTRUDER_HOMING_SPEED', self.extruder_homing_speed, above=1.)
        self.extruder_load_speed = gcmd.get_float('EXTRUDER_LOAD_SPEED', self.extruder_load_speed, above=1.)
        self.extruder_unload_speed = gcmd.get_float('EXTRUDER_UNLOAD_SPEED', self.extruder_unload_speed, above=1.)
        self.extruder_sync_load_speed = gcmd.get_float('EXTRUDER_SYNC_LOAD_SPEED', self.extruder_sync_load_speed, above=1.)
        self.extruder_sync_unload_speed = gcmd.get_float('EXTRUDER_SYNC_UNLOAD_SPEED', self.extruder_sync_unload_speed, above=1.)
        # Selector speeds
        self.selector_move_speed = gcmd.get_float('SELECTOR_MOVE_SPEED', self.selector_move_speed, minval=1.)
        self.selector_homing_speed = gcmd.get_float('SELECTOR_HOMING_SPEED', self.selector_homing_speed, minval=1.)
        self.selector_touch_speed = gcmd.get_float('SELECTOR_TOUCH_SPEED', self.selector_touch_speed, minval=1.)

        # Synchronous motor control
        self.sync_form_tip = gcmd.get_int('SYNC_FORM_TIP', self.sync_form_tip, minval=0, maxval=1)
        self.sync_load_extruder = gcmd.get_int('SYNC_LOAD_EXTRUDER', self.sync_load_extruder, minval=0, maxval=1)
        self.sync_unload_extruder = gcmd.get_int('SYNC_UNLOAD_EXTRUDER', self.sync_unload_extruder, minval=0, maxval=1)
        self.sync_to_extruder = gcmd.get_int('SYNC_TO_EXTRUDER', self.sync_to_extruder, minval=0, maxval=1)

        # TMC current control
        self.sync_gear_current = gcmd.get_int('SYNC_GEAR_CURRENT', self.sync_gear_current, minval=10, maxval=100)
        self.extruder_homing_current = gcmd.get_int('EXTRUDER_HOMING_CURRENT', self.extruder_homing_current, minval=10, maxval=100)
        self.extruder_form_tip_current = gcmd.get_int('EXTRUDER_FORM_TIP_CURRENT', self.extruder_form_tip_current, minval=100, maxval=150)

        # Homing, loading and unloading controls
        self.apply_bowden_correction = gcmd.get_int('APPLY_BOWDEN_CORRECTION', self.apply_bowden_correction, minval=0, maxval=1)
        self.unload_bowden_tolerance = self.load_bowden_tolerance = gcmd.get_float('LOAD_BOWDEN_TOLERANCE', self.load_bowden_tolerance, minval=1., maxval=50.)
        self.home_to_extruder = gcmd.get_int('HOME_TO_EXTRUDER', self.home_to_extruder, minval=0, maxval=1)
        self.extruder_homing_endstop = gcmd.get('EXTRUDER_HOMING_ENDSTOP', self.extruder_homing_endstop)
        self.extruder_homing_max = gcmd.get_float('EXTRUDER_HOMING_MAX', self.extruder_homing_max, above=10.)
        self.toolhead_homing_max = gcmd.get_float('TOOLHEAD_HOMING_MAX', self.toolhead_homing_max, minval=0.)
        self.transition_length = gcmd.get_float('TRANSITION_LENGTH', self.transition_length, minval=0., maxval=50.)
        self.delay_servo_release = gcmd.get_float('DELAY_SERVO_RELEASE', self.delay_servo_release, minval=0., maxval=5.)
        self.home_position_to_nozzle = gcmd.get_float('HOME_POSITION_TO_NOZZLE', self.home_position_to_nozzle, minval=5.)
        self.extruder_to_nozzle = gcmd.get_float('EXTRUDER_TO_NOZZLE', self.extruder_to_nozzle, minval=0.)
        self.sensor_to_nozzle = gcmd.get_float('SENSOR_TO_NOZZLE', self.sensor_to_nozzle, minval=0.)
        self.ignore_extruder_load_error = gcmd.get_int('IGNORE_EXTRUDER_LOAD_ERROR', self.ignore_extruder_load_error, minval=0, maxval=1)

        # Software behavior options
        self.z_hop_height = gcmd.get_float('Z_HOP_HEIGHT', self.z_hop_height, minval=0.)
        self.z_hop_speed = gcmd.get_float('Z_HOP_SPEED', self.z_hop_speed, minval=1.)
        self.enable_selector_touch = gcmd.get_int('ENABLE_SELECTOR_TOUCH', self.enable_selector_touch, minval=0, maxval=1)
        self.selector_touch = self.SELECTOR_TOUCH_ENDSTOP in self.selector_stepper.get_endstop_names() and self.enable_selector_touch
        self.enable_clog_detection = gcmd.get_int('ENABLE_CLOG_DETECTION', self.enable_clog_detection, minval=0, maxval=2)
        self.encoder_sensor.set_mode(self.enable_clog_detection)
        self.enable_endless_spool = gcmd.get_int('ENABLE_ENDLESS_SPOOL', self.enable_endless_spool, minval=0, maxval=1)
        self.log_level = gcmd.get_int('LOG_LEVEL', self.log_level, minval=0, maxval=4)
        self.log_visual = gcmd.get_int('LOG_VISUAL', self.log_visual, minval=0, maxval=2)
        self.log_statistics = gcmd.get_int('LOG_STATISTICS', self.log_statistics, minval=0, maxval=1)

        # Calibration
        self.calibrated_bowden_length = gcmd.get_float('MMU_CALIBRATION_BOWDEN_LENGTH', self.calibrated_bowden_length, minval=10.)
        clog_length = gcmd.get_float('MMU_CALIBRATION_CLOG_LENGTH', self.encoder_sensor.get_clog_detection_length(), minval=1., maxval=100.)
        if clog_length != self.encoder_sensor.get_clog_detection_length():
            self.encoder_sensor.set_clog_detection_length(clog_length)

        msg = "Speeds:"
        msg += "\ngear_short_move_speed = %.1f" % self.gear_short_move_speed
        msg += "\ngear_from_buffer_speed = %.1f" % self.gear_from_buffer_speed
        msg += "\ngear_from_spool_speed = %.1f" % self.gear_from_spool_speed
        msg += "\ngear_homing_speed = %.1f" % self.gear_homing_speed
        msg += "\nextruder_homing_speed = %.1f" % self.extruder_homing_speed
        msg += "\nextruder_load_speed = %.1f" % self.extruder_load_speed
        msg += "\nextruder_unload_speed = %.1f" % self.extruder_unload_speed
        msg += "\nextruder_sync_load_speed = %.1f" % self.extruder_sync_load_speed
        msg += "\nextruder_sync_unload_speed = %.1f" % self.extruder_sync_unload_speed
        msg += "\nselector_move_speed = %.1f" % self.selector_move_speed
        msg += "\nselector_homing_speed = %.1f" % self.selector_homing_speed
        msg += "\nselector_touch_speed = %.1f" % self.selector_touch_speed

        msg += "\n\nSynchronous motor control:"
        msg += "\nsync_to_extruder = %d" % self.sync_to_extruder
        msg += "\nsync_load_extruder = %d" % self.sync_load_extruder
        msg += "\nsync_unload_extruder = %d" % self.sync_unload_extruder
        msg += "\nsync_form_tip = %d" % self.sync_form_tip

        msg += "\n\nTMC current control:"
        msg += "\nsync_gear_current = %d" % self.sync_gear_current
        msg += "\nextruder_homing_current = %d" % self.extruder_homing_current
        msg += "\nextruder_form_tip_current = %d" % self.extruder_form_tip_current

        msg += "\n\nLoad, unload, home controls:"
        msg += "\napply_bowden_correction = %d" % self.apply_bowden_correction
        msg += "\nload_bowden_tolerance = %d" % self.load_bowden_tolerance
        msg += "\nhome_to_extruder = %d" % self.home_to_extruder
        msg += "\nextruder_homing_endstop = %s" % self.extruder_homing_endstop
        msg += "\nextruder_homing_max = %.1f" % self.extruder_homing_max
        msg += "\ntoolhead_homing_max = %.1f" % self.toolhead_homing_max
        msg += "\ntransition_length = %.1f" % self.transition_length
        msg += "\ndelay_servo_release = %.1f" % self.delay_servo_release
        msg += "\nhome_position_to_nozzle = %.1f" % self.home_position_to_nozzle
        if self.extruder_to_nozzle > 0.:
            msg += "\nextruder_to_nozzle = %.1f" % self.extruder_to_nozzle
        if self.sensor_to_nozzle > 0.:
            msg += "\nsensor_to_nozzle = %.1f" % self.sensor_to_nozzle
        msg += "\nignore_extruder_load_error = %d" % self.ignore_extruder_load_error

        msg += "\n\nOther behavior:"
        msg += "\nz_hop_height = %.1f" % self.z_hop_height
        msg += "\nz_hop_speed = %.1f" % self.z_hop_speed
        msg += "\nenable_selector_touch = %d" % self.enable_selector_touch
        msg += "\nenable_clog_detection = %d" % self.enable_clog_detection
        msg += "\nenable_endless_spool = %d" % self.enable_endless_spool
        msg += "\nlog_level = %d" % self.log_level
        msg += "\nlog_visual = %d" % self.log_visual
        msg += "\nlog_statistics = %d" % self.log_statistics

        msg += "\n\nCalibration:"
        msg += "\nmmu_calibration_bowden_length = %.1f" % self.calibrated_bowden_length
        msg += "\nmmu_calibration_clog_length = %.1f" % clog_length
        self._log_info(msg)


###########################################
# RUNOUT, ENDLESS SPOOL and GATE HANDLING #
###########################################

    def _handle_runout(self, force_runout):
        if self._check_is_paused(): return
        if self.tool_selected < 0:
            raise MmuError("Filament runout or clog on an unknown or bypass tool - manual intervention is required")

        self._log_info("Issue on tool T%d" % self.tool_selected)
        self._save_toolhead_position_and_lift()

        # Check for clog by looking for filament in the encoder
        self._log_debug("Checking if this is a clog or a runout (state %d)..." % self.loaded_status)
        self._servo_down()
        found = self._buzz_gear_motor()
        self._servo_up()
        if found and not force_runout:
            self._disable_encoder_sensor(update_clog_detection_length=True)
            raise MmuError("A clog has been detected and requires manual intervention")

        # We have a filament runout
        self._disable_encoder_sensor()
        self._log_always("A runout has been detected")
        if self.enable_endless_spool:
            group = self.endless_spool_groups[self.gate_selected]
            self._log_info("EndlessSpool checking for additional spools in group %d..." % group)
            self._set_gate_status(self.gate_selected, self.GATE_EMPTY) # Indicate current gate is empty
            next_gate = -1
            checked_gates = []
            for i in range(self.num_gates - 1):
                check = (self.gate_selected + i + 1) % self.num_gates
                if self.endless_spool_groups[check] == group:
                    checked_gates.append(check)
                    if self.gate_status[check] != self.GATE_EMPTY:
                        next_gate = check
                        break
            if next_gate == -1:
                self._log_info("No more available spools found in ES_Group_%d - manual intervention is required" % self.endless_spool_groups[self.tool_selected])
                self._log_info(self._tool_to_gate_map_to_human_string())
                raise MmuError("No more EndlessSpool spools available after checking gates %s" % checked_gates)
            self._log_info("Remapping T%d to gate #%d" % (self.tool_selected, next_gate))

            self.gcode.run_script_from_command("_MMU_ENDLESS_SPOOL_PRE_UNLOAD")
            detected, park_pos = self._form_tip_standalone(extruder_stepper_only=True)
            if not detected:
                self._log_info("Filament didn't reach encoder after tip forming move")
            self._unload_tool(skip_tip=True)
            self._remap_tool(self.tool_selected, next_gate, 1)
            self._select_and_load_tool(self.tool_selected)
            self.gcode.run_script_from_command("_MMU_ENDLESS_SPOOL_POST_LOAD")
            self._restore_toolhead_position()
            self.encoder_sensor.reset_counts()    # Encoder 0000
            self._enable_encoder_sensor()
            if self.sync_to_extruder:
                self._sync_gear_to_extruder(True, servo=True, in_print=True)
            # Continue printing...
        else:
            raise MmuError("EndlessSpool mode is off - manual intervention is required")

    def _set_tool_to_gate(self, tool, gate):
        self.tool_to_gate_map[tool] = gate
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_TOOL_TO_GATE_MAP, self.tool_to_gate_map))

    def _set_gate_status(self, gate, state):
        self.gate_status[gate] = state
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_GATE_STATUS, self.gate_status))

    def _get_filament_char(self, gate_status, no_space=False, show_source=False):
        if gate_status == self.GATE_AVAILABLE_FROM_BUFFER:
            return "B" if show_source else "*"
        elif gate_status == self.GATE_AVAILABLE:
            return "S" if show_source else "*"
        elif gate_status == self.GATE_EMPTY:
            return ("." if no_space else " ")
        else:
            return "?"

    def _tool_to_gate_map_to_human_string(self, summary=False):
        msg = ""
        if not summary:
            num_tools = self.num_gates
            for i in range(num_tools): # Tools
                msg += "\n" if i else ""
                gate = self.tool_to_gate_map[i]
                msg += "%s-> Gate #%d%s" % (("T%d " % i)[:3], gate, "(" + self._get_filament_char(self.gate_status[gate], show_source=True) + ")")
                if self.enable_endless_spool:
                    group = self.endless_spool_groups[gate]
                    es = " ES_Group_%s: " % group
                    prefix = ""
                    starting_gate = self.tool_to_gate_map[i]
                    for j in range(num_tools): # Gates
                        gate = (j + starting_gate) % num_tools
                        if self.endless_spool_groups[gate] == group:
                            es += "%s%d%s" % (prefix, gate,self._get_filament_char(self.gate_status[gate]))
                            prefix = "> "
                    msg += es
                if i == self.tool_selected:
                    msg += " [SELECTED on gate #%d]" % self.gate_selected
            msg += "\n\n"
            msg += self._gate_map_to_human_string(True)
        else:
            multi_tool = False
            msg_gates = "Gates: "
            msg_avail = "Avail: "
            msg_tools = "Tools: "
            msg_selct = "Selct: "
            for g in range(self.num_gates):
                msg_gates += ("|#%d " % g)[:4]
                msg_avail += "| %s " % self._get_filament_char(self.gate_status[g], no_space=True, show_source=True)
                tool_str = ""
                prefix = ""
                for t in range(self.num_gates):
                    if self.tool_to_gate_map[t] == g:
                        if len(prefix) > 0: multi_tool = True
                        tool_str += "%sT%d" % (prefix, t)
                        prefix = "+"
                if tool_str == "": tool_str = " . "
                msg_tools += ("|%s " % tool_str)[:4]
                if self.gate_selected == g:
                    msg_selct += ("| %s " % self._get_filament_char(self.gate_status[g], no_space=True))
                else:
                    msg_selct += "|---" if self.gate_selected != self.GATE_UNKNOWN and self.gate_selected == (g - 1) else "----"
            msg += msg_gates
            msg += "|\n"
            msg += msg_tools
            msg += "|%s\n" % (" Some gates support multiple tools!" if multi_tool else "")
            msg += msg_avail
            msg += "|\n"
            msg += msg_selct
            msg += "|" if self.gate_selected == self.num_gates - 1 else "-"
            msg += " Bypass" if self.gate_selected == self.TOOL_BYPASS else (" T%d" % self.tool_selected) if self.tool_selected >= 0 else ""
        return msg

    def _gate_map_to_human_string(self, detail=False):
        msg = "MMU Gates / Filaments:"
        for g in range(self.num_gates):
            material = self.gate_material[g] if self.gate_material[g] != "" else "n/a"
            color = self.gate_color[g] if self.gate_color[g] != "" else "n/a"
            available = {
                self.GATE_AVAILABLE_FROM_BUFFER: "Buffered", 
                self.GATE_AVAILABLE: "Available",
                self.GATE_EMPTY: "Empty",
                self.GATE_UNKNOWN: "Unknown"
            }[self.gate_status[g]]
            if detail:
                msg += "\nGate #%d%s" % (g, "(" + self._get_filament_char(self.gate_status[g], show_source=True) + ")")
                tool_str = " -> "
                prefix = ""
                for t in range(self.num_gates):
                    if self.tool_to_gate_map[t] == g:
                        tool_str += "%sT%d" % (prefix, t)
                        prefix = ","
                msg += tool_str
                msg += "?, " if prefix == "" else ", "
            else:
                msg += ("\nGate #%d: " % g)
            msg += ("Material: %s, Color: %s, Status: %s" % (material, color, available))
            if detail and g == self.gate_selected:
                msg += " [SELECTED%s]" % ((" supporting tool T%d" % self.tool_selected) if self.tool_selected >= 0 else "")
        return msg

    def _remap_tool(self, tool, gate, available):
        self._set_tool_to_gate(tool, gate)
        self._set_gate_status(gate, available)

    def _reset_ttg_mapping(self):
        self._log_debug("Resetting TTG map")
        self.tool_to_gate_map = self.default_tool_to_gate_map
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_TOOL_TO_GATE_MAP, self.tool_to_gate_map))
        self._unselect_tool()

    def _reset_gate_map(self):
        self._log_debug("Resetting Gate/Filament map")
        self.gate_status = self.default_gate_status
        self.gate_material = self.default_gate_material
        self.gate_color = self.default_gate_color
        self._persist_gate_map()

    def _validate_color(self, color):
        color = color.lower()
        if color == "":
            return True

        # Try w3c named color
        for i in range(len(self.W3C_COLORS)):
            if color == self.W3C_COLORS[i]:
                return True

        # Try RGB color
        color = "#" + color
        x = re.search("^#?([a-f\d]{6})$", color)
        if x != None and x.group() == color:
            return True

        return False


### GCODE COMMANDS FOR RUNOUT, TTG MAP, GATE MAP and GATE LOGIC ##################################

    cmd_MMU_ENCODER_RUNOUT_help = "Internal encoder filament runout handler"
    def cmd_MMU_ENCODER_RUNOUT(self, gcmd):
        if self._check_is_disabled(): return
        force_runout = bool(gcmd.get_int('FORCE_RUNOUT', 0, minval=0, maxval=1))
        try:
            self._handle_runout(force_runout)
        except MmuError as ee:
            self._pause(str(ee))

    cmd_MMU_ENCODER_INSERT_help = "Internal encoder filament detection handler"
    def cmd_MMU_ENCODER_INSERT(self, gcmd):
        if self._check_is_disabled(): return
        self._log_debug("Filament insertion not implemented yet! Check back later")
# Future feature :-)
#        try:
#            self._handle_detection()
#        except MmuError as ee:
#            self._pause(str(ee))

    cmd_MMU_REMAP_TTG_help = "Remap a tool to a specific gate and set gate availability"
    def cmd_MMU_REMAP_TTG(self, gcmd):
        if self._check_is_disabled(): return
        quiet = gcmd.get_int('QUIET', 0, minval=0, maxval=1)
        reset = gcmd.get_int('RESET', 0, minval=0, maxval=1)
        ttg_map = gcmd.get('MAP', "")
        if reset == 1:
            self._reset_ttg_mapping()
        elif ttg_map != "":
            ttg_map = gcmd.get('MAP').split(",")
            if len(ttg_map) != self.num_gates:
                self._log_always("The number of map values (%d) is not the same as number of gates (%d)" % (len(ttg_map), self.num_gates))
                return
            self.tool_to_gate_map = []
            for gate in ttg_map:
                if gate.isdigit():
                    self.tool_to_gate_map.append(int(gate))
                else:
                    self.tool_to_gate_map.append(0)
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_TOOL_TO_GATE_MAP, self.tool_to_gate_map))
        else:
            gate = gcmd.get_int('GATE', -1, minval=0, maxval=self.num_gates - 1)
            tool = gcmd.get_int('TOOL', -1, minval=0, maxval=self.num_gates - 1)
            available = gcmd.get_int('AVAILABLE', -1, minval=0, maxval=1)
            if gate != -1:
                if available == -1:
                    available = self.gate_status[gate]
                if tool == -1:
                    self._set_gate_status(gate, available)
                else:
                    self._remap_tool(tool, gate, available)
        if not quiet:
            self._log_info(self._tool_to_gate_map_to_human_string())

    cmd_MMU_SET_GATE_MAP_help = "Define the type and color of filaments on each gate"
    def cmd_MMU_SET_GATE_MAP(self, gcmd):
        quiet = gcmd.get_int('QUIET', 0, minval=0, maxval=1)
        reset = gcmd.get_int('RESET', 0, minval=0, maxval=1)
        dump = gcmd.get_int('DISPLAY', 0, minval=0, maxval=1)
        if reset == 1:
            self._reset_gate_map()
        elif dump == 1:
            self._log_info(self._gate_map_to_human_string())
            return
        else:
            # Specifying one gate (filament)
            gate = gcmd.get_int('GATE', minval=0, maxval=self.num_gates - 1)
            material = "".join(gcmd.get('MATERIAL').split()).replace('#', '').upper()[:10]
            color = "".join(gcmd.get('COLOR').split()).replace('#', '').lower()
            available = gcmd.get_int('AVAILABLE', self.gate_status[gate], minval=0, maxval=1)
            if not self._validate_color(color):
                raise gcmd.error("Color specification must be in form 'rrggbb' hexadecimal value (no '#') or valid color name or empty string")
            self.gate_material[gate] = material
            self.gate_color[gate] = color
            self.gate_status[gate] = available
            self._persist_gate_map()

        if not quiet:
            self._log_info(self._gate_map_to_human_string())

    cmd_MMU_ENDLESS_SPOOL_help = "Redefine the EndlessSpool groups"
    def cmd_MMU_ENDLESS_SPOOL(self, gcmd):
        if self._check_is_disabled(): return
        quiet = gcmd.get_int('QUIET', 0, minval=0, maxval=1)
        enabled = gcmd.get_int('ENABLE', -1, minval=0, maxval=1)
        reset = gcmd.get_int('RESET', 0, minval=0, maxval=1)
        dump = gcmd.get_int('DISPLAY', 0, minval=0, maxval=1)
        if enabled >= 0:
            self.enable_endless_spool = enabled
            self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%d" % (self.VARS_MMU_ENABLE_ENDLESS_SPOOL, self.enable_endless_spool))
        if not self.enable_endless_spool:
            self._log_always("EndlessSpool is disabled")
        if reset == 1:
            self._log_debug("Resetting EndlessSpool groups")
            self.enable_endless_spool = self.default_enable_endless_spool
            self.endless_spool_groups = self.default_endless_spool_groups
        elif dump == 1:
            self._log_info(self._tool_to_gate_map_to_human_string())
            return
        else:
            groups = gcmd.get('GROUPS', ",".join(map(str, self.endless_spool_groups))).split(",")
            if len(groups) != self.num_gates:
                self._log_always("The number of group values (%d) is not the same as number of gates (%d)" % (len(groups), self.num_gates))
                return
            self.endless_spool_groups = []
            for group in groups:
                if group.isdigit():
                    self.endless_spool_groups.append(int(group))
                else:
                    self.endless_spool_groups.append(0)
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE='%s'" % (self.VARS_MMU_ENDLESS_SPOOL_GROUPS, self.endless_spool_groups))

        if not quiet:
            self._log_info(self._tool_to_gate_map_to_human_string())

    cmd_MMU_CHECK_GATES_help = "Automatically inspects gate(s), parks filament and marks availability"
    def cmd_MMU_CHECK_GATES(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_not_homed(): return
        if self._check_in_bypass(): return
        if self._check_is_loaded(): return

        quiet = gcmd.get_int('QUIET', 0, minval=0, maxval=1)
        # These three parameters are mutually exclusive so we only process one
        tools = gcmd.get('TOOLS', "!")
        tool = gcmd.get_int('TOOL', -1, minval=0, maxval=self.num_gates - 1)
        gate = gcmd.get_int('GATE', -1, minval=0, maxval=self.num_gates - 1)
        current_action = self._set_action(self.ACTION_CHECKING)
        try:
            tool_selected = self.tool_selected
            self._set_tool_selected(self.TOOL_UNKNOWN)
            gates_tools = []
            if tools != "!":
                # Tools used in print (may be empty list)
                try:
                    for tool in tools.split(','):
                        gate = int(self.tool_to_gate_map[int(tool)])
                        gates_tools.append([gate, int(tool)])
                    if len(gates_tools) == 0:
                        self._log_debug("No tools to check, assuming default tool is already loaded")
                        return
                except ValueError as ve:
                    msg = "Invalid TOOLS parameter: %s" % tools
                    if self._is_in_print():
                        self._pause(msg)
                    else:
                        self._log_always(msg)
                    return
            elif tool >= 0:
                # Individual tool
                gate = self.tool_to_gate_map[tool]
                gates_tools.append([gate, tool])
            elif gate >= 0:
                # Individual gate
                gates_tools.append([gate, -1])
            else :
                # No parameters means all gates
                for gate in range(self.num_gates):
                    gates_tools.append([gate, -1])

            for gate, tool in gates_tools:
                try:
                    self._select_gate(gate)
                    self.encoder_sensor.reset_counts()    # Encoder 0000
                    self.calibrating = True # To suppress visual filament position
                    self._log_info("Checking gate #%d..." % gate)
                    encoder_moved = self._load_encoder(retry=False, adjust_servo_on_error=False)
                    if tool >= 0:
                        self._log_info("Tool T%d - filament detected. Gate #%d marked available" % (tool, gate))
                    else:
                        self._log_info("Gate #%d - filament detected. Marked available" % gate)
                    self._set_gate_status(gate, max(self.gate_status[gate], self.GATE_AVAILABLE))
                    try:
                        if encoder_moved > self.encoder_min:
                            self._unload_encoder(self.unload_buffer)
                        else:
                            self._set_loaded_status(self.LOADED_STATUS_UNLOADED, silent=True)
                    except MmuError as ee:
                        msg = "Failure during check gate #%d %s: %s" % (gate, "(T%d)" % tool if tool >= 0 else "", str(ee))
                        if self._is_in_print():
                            self._pause(msg)
                        else:
                            self._log_always(msg)
                        return
                except MmuError as ee:
                    self._set_gate_status(gate, self.GATE_EMPTY)
                    self._set_loaded_status(self.LOADED_STATUS_UNLOADED, silent=True)
                    if tool >= 0:
                        msg = "Tool T%d - filament not detected. Gate #%d marked empty" % (tool, gate)
                    else:
                        msg = "Gate #%d - filament not detected. Marked empty" % gate
                    if self._is_in_print():
                        self._pause(msg)
                    else:
                        self._log_info(msg)
                finally:
                    self.calibrating = False

            try:
                if tool_selected == self.TOOL_BYPASS:
                    self._select_bypass()
                elif tool_selected != self.TOOL_UNKNOWN:
                    self._select_tool(tool_selected)
            except MmuError as ee:
                self._log_always("Failure re-selecting Tool %d: %s" % (tool_selected, str(ee)))

            if not quiet:
                self._log_info(self._tool_to_gate_map_to_human_string(summary=True))
        finally:
            self._set_action(current_action)
            self._servo_auto()

    cmd_MMU_PRELOAD_help = "Preloads filament at specified or current gate"
    def cmd_MMU_PRELOAD(self, gcmd):
        if self._check_is_disabled(): return
        if self._check_not_homed(): return
        if self._check_in_bypass(): return
        if self._check_is_loaded(): return
        gate = gcmd.get_int('GATE', -1, minval=0, maxval=self.num_gates - 1)
        current_action = self._set_action(self.ACTION_CHECKING)
        try:
            self.calibrating = True # To suppress visual filament position display
            # If gate not specified assume current gate
            if gate == -1:
                gate = self.gate_selected
            else:
                self._select_gate(gate)
            self.encoder_sensor.reset_counts()    # Encoder 0000
            for i in range(5):
                self._log_always("Loading...")
                try:
                    self._load_encoder(retry=False, adjust_servo_on_error=False)
                    # Caught the filament, so now park it in the gate
                    self._log_always("Parking...")
                    self._unload_encoder(self.unload_buffer)
                    self._log_always("Filament detected and parked in gate #%d" % gate)
                    return
                except MmuError as ee:
                    # Exception just means filament is not loaded yet, so continue
                    self._log_trace("Exception on encoder load move: %s" % str(ee))
                    pass
            self._set_gate_status(gate, self.GATE_EMPTY)
            self._log_always("Filament not detected in gate #%d" % gate)
        except MmuError as ee:
            self._log_always("Filament preload for gate #%d failed: %s" % (gate, str(ee)))
        finally:
            self.calibrating = False
            self._servo_auto()
            self._set_action(current_action)

def load_config(config):
    return Mmu(config)

