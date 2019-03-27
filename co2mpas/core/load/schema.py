# -*- coding: utf-8 -*-
#
# Copyright 2015-2019 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl
"""
It provides CO2MPAS schema parse/validator.
"""

import re
import pprint
import logging
import functools
import numpy as np
import os.path as osp
import schedula as sh
from collections import Iterable, OrderedDict
from schema import Schema, Use, And, Or, Optional, SchemaError

log = logging.getLogger(__name__)


# noinspection PyMissingOrEmptyDocstring
class Empty:
    def __repr__(self):
        return '%s' % self.__class__.__name__

    @staticmethod
    def validate(data):
        if isinstance(data, str) and data == 'EMPTY':
            return sh.EMPTY

        try:
            empty = not (data or data == 0)
        except ValueError:
            empty = np.isnan(data).all()

        if empty:
            return sh.NONE
        else:
            raise SchemaError('%r is not empty' % data)


# noinspection PyUnusedLocal
def _function(error=None, read=True, **kwargs):
    def _check_function(f):
        assert callable(f)
        return f

    if read:
        error = error or 'should be a function!'
        return _eval(Use(_check_function), error=error)
    return And(_check_function, Use(lambda x: sh.NONE), error=error)


# noinspection PyUnusedLocal
def _string(error=None, **kwargs):
    error = error or 'should be a string!'
    return Use(str, error=error)


# noinspection PyUnusedLocal
def _select(types=(), error=None, **kwargs):
    error = error or 'should be one of {}!'.format(types)
    types = {k.lower(): k for k in types}
    return And(str, Use(lambda x: types[x.lower()]), error=error)


def _check_positive(x):
    return x >= 0


# noinspection PyUnusedLocal,PyShadowingBuiltins
def _positive(type=float, error=None, check=_check_positive, **kwargs):
    error = error or 'should be as {} and positive!'.format(type)
    return And(Use(type), check, error=error)


# noinspection PyUnusedLocal
def _limits(limits=(0, 100), error=None, **kwargs):
    error = error or 'should be {} <= x <= {}!'.format(*limits)

    def _check_limits(x):
        return limits[0] <= x <= limits[1]

    return And(Use(float), _check_limits, error=error)


# noinspection PyUnusedLocal
def _eval(s, error=None, **kwargs):
    error = error or 'cannot be eval!'
    from asteval import Interpreter
    return Or(And(str, Use(Interpreter().eval), s), s, error=error)


# noinspection PyUnusedLocal,PyShadowingBuiltins
def _dict(format=None, error=None, read=True, pformat=pprint.pformat, **kwargs):
    format = And(dict, format or {int: float})
    error = error or 'should be a dict with this format {}!'.format(format)
    c = Use(lambda x: {k: v for k, v in dict(x).items() if v is not None})
    if read:
        return _eval(Or(Empty(), And(c, Or(Empty(), format))), error=error)
    else:
        return And(_dict(format=format, error=error), Use(pformat))


# noinspection PyUnusedLocal,PyShadowingBuiltins
def _ordict(format=None, error=None, read=True, **kwargs):
    format = format or {int: float}
    msg = 'should be a OrderedDict with this format {}!'
    error = error or msg.format(format)
    c = Use(OrderedDict)
    if read:
        return _eval(Or(Empty(), And(c, Or(Empty(), format))), error=error)
    else:
        return And(_dict(format=format, error=error), Use(pprint.pformat))


def _check_length(length):
    if not isinstance(length, Iterable):
        length = (length,)

    def _check(data):
        ld = len(data)
        return any(ld == l for l in length)

    return _check


# noinspection PyUnusedLocal,PyShadowingBuiltins
def _type(type=None, error=None, length=None, **kwargs):
    type = type or tuple

    if length is not None:
        error = error or 'should be as {} and ' \
                         'with a length of {}!'.format(type, length)
        return And(_type(type=type), _check_length(length), error=error)
    if not isinstance(type, (Use, Schema, And, Or)):
        type = Or(type, Use(type))
    error = error or 'should be as {}!'.format(type)
    return _eval(type, error=error)


# noinspection PyUnusedLocal
def _index_dict(error=None, **kwargs):
    error = error or 'cannot be parsed as {}!'.format({int: float})
    c = {int: Use(float)}
    s = And(dict, c)

    def _f(x):
        return {k: v for k, v in enumerate(x, start=1)}

    return Or(s, And(_dict(), c), And(_type(), Use(_f), c), error=error)


# noinspection PyUnusedLocal
def _np_array(dtype=None, error=None, read=True, **kwargs):
    dtype = dtype or float
    error = error or 'cannot be parsed as np.array dtype={}!'.format(dtype)
    if read:
        c = Use(lambda x: np.asarray(x, dtype=dtype))
        return Or(And(str, _eval(c)), c, And(_type(), c), Empty(), error=error)
    else:
        return And(_np_array(dtype=dtype), Use(lambda x: x.tolist()),
                   error=error)


def _check_np_array_positive(x):
    """

    :param x:
        Array.
    :type x: numpy.array
    :return:
    """
    # noinspection PyUnresolvedReferences
    return (x >= 0).all()


# noinspection PyUnusedLocal
def _np_array_positive(dtype=None, error=None, read=True,
                       check=_check_np_array_positive, **kwargs):
    dtype = dtype or float
    error = error or 'cannot be parsed because it should be an ' \
                     'np.array dtype={} and positive!'.format(dtype)
    if read:
        c = And(Use(lambda x: np.asarray(x, dtype=dtype)), check)
        return Or(And(str, _eval(c)), c, And(_type(), c), Empty(),
                  error=error)
    else:
        return And(_np_array_positive(dtype=dtype), Use(lambda x: x.tolist()),
                   error=error)


# noinspection PyUnusedLocal
def _cold_start_speed_model(error=None, **kwargs):
    from ..model.physical.engine.cold_start import ColdStartModel
    return _type(type=ColdStartModel, error=error)


# noinspection PyUnusedLocal
def _cmv(error=None, **kwargs):
    from ..model.physical.gear_box.at_gear.cmv import CMV
    return _type(type=CMV, error=error)


# noinspection PyUnusedLocal
def _mvl(error=None, **kwargs):
    from ..model.physical.gear_box.at_gear import MVL
    return _type(type=MVL, error=error)


# noinspection PyUnusedLocal
def _gspv(error=None, **kwargs):
    from ..model.physical.gear_box.at_gear.gspv import GSPV
    return _type(type=GSPV, error=error)


# noinspection PyUnusedLocal
def _gsch(error=None, **kwargs):
    from ..model.physical.gear_box.at_gear.gspv_ch import GSMColdHot
    return _type(type=GSMColdHot, error=error)


# noinspection PyUnusedLocal
def _dtc(error=None, read=True, **kwargs):
    if read:
        from ..model.physical.gear_box.at_gear.dtgs import DTGS
        return _type(type=DTGS, error=error)
    return And(_dtc(), Use(lambda x: sh.NONE), error=error)


# noinspection PyUnusedLocal
def _cvt(error=None, read=True, **kwargs):
    if read:
        from ..model.physical.gear_box.cvt import CVT
        return _type(type=CVT, error=error)
    return And(_dtc(), Use(lambda x: sh.NONE), error=error)


def _parameters2str(data):
    from lmfit import Parameters
    if isinstance(data, Parameters):
        return data.dumps(sort_keys=True)


def _str2parameters(data):
    if isinstance(data, str):
        from lmfit import Parameters
        p = Parameters()
        p.loads(data)
        return p
    return data


def _parameters(error=None, read=True):
    if read:
        from lmfit import Parameters
        return And(Use(_str2parameters), _type(type=Parameters, error=error))
    else:
        return And(_parameters(), Use(_parameters2str), error=error)


# noinspection PyUnusedLocal
def _compare_str(s, **kwargs):
    return And(Use(str.lower), s.lower(), Use(lambda x: s))


# noinspection PyUnusedLocal
def _convert_str(old_str, new_str, **kwargs):
    return And(Use(str), Or(old_str, new_str), Use(lambda x: new_str))


# noinspection PyUnusedLocal
def _tyre_code(error=None, **kwargs):
    error = error or 'invalid tyre code!'
    # noinspection PyProtectedMember
    from ..model.physical.wheels import _re_tyre_code_iso, _re_tyre_code_numeric
    c = Or(_re_tyre_code_iso.match, _re_tyre_code_numeric.match)
    return And(str, c, error=error)


# noinspection PyUnusedLocal
def _tyre_dimensions(error=None, **kwargs):
    error = error or 'invalid format for tyre dimensions!'
    # noinspection PyProtectedMember
    from ..model.physical.wheels import _format_tyre_dimensions
    return And(_dict(format=dict), Use(_format_tyre_dimensions), error=error)


def check_phases_separated(x):
    """
    >>> bags = [
        [3, 2, 3, 2, 4, 4, 1, 1], # INVALID!
        [3, 2, 2, 2, 4, 4, 1, 4], # INVALID!
        [3, 3, 2, 2, 4, 4, 1, 1], # valid
        ['P1', 'P3', 'P3', 'P2'], # valid
        [False, False],           # valid
        [],                       # valid
    ]
    >>> [check_phases_separated(x) for x in bags]
    [False, False, True, True, True, True]
    """
    if not len(x):
        return True

    x = np.asarray(x)
    # noinspection PyUnresolvedReferences
    deduped_count = 1 + (x[1:] != x[:-1]).sum()  # [3,3,1,1,3] --> len([3,1,3])

    return deduped_count == np.unique(x).size


# noinspection PyUnusedLocal
def _bag_phases(error=None, read=True, **kwargs):
    er = 'Phases must be separated!'
    if read:
        return And(_np_array(read=read),
                   Schema(check_phases_separated, error=er), error=error)
    else:
        return And(_bag_phases(error), _np_array(read=False), error=error)


# noinspection PyUnusedLocal
def _file(error=None, **kwargs):
    er = 'Must be a file!'
    return And(_string(), Schema(osp.isfile, error=er), error=error)


# noinspection PyUnusedLocal
def _dir(error=None, **kwargs):
    er = 'Must be a directory!'
    fun = lambda x: not osp.exists(x) or osp.isdir(x)
    return And(_string(), Schema(fun, error=er), error=error)


def _is_sorted(iterable, key=lambda a, b: a <= b):
    return all(key(a, b) for a, b in sh.pairwise(iterable))


# noinspection PyUnresolvedReferences
@functools.lru_cache(None)
def define_data_schema(read=True):
    """
    Define data schema.

    :param read:
        Schema for reading?
    :type read: bool

    :return:
        Data schema.
    :rtype: schema.Schema
    """
    cssm = _cold_start_speed_model(read=read)
    cmv = _cmv(read=read)
    dtc = _dtc(read=read)
    cvt = _cvt(read=read)
    gspv = _gspv(read=read)
    gsch = _gsch(read=read)
    string = _string(read=read)
    positive = _positive(read=read)
    greater_than_zero = _positive(
        read=read, error='should be as <float> and greater than zero!',
        check=lambda x: x > 0
    )
    greater_than_one = _positive(
        read=read, error='should be as <float> and greater than one!',
        check=lambda x: x >= 1
    )
    positive_int = _positive(type=int, read=read)
    limits = _limits(read=read)
    index_dict = _index_dict(read=read)
    np_array = _np_array(read=read)
    np_array_sorted = _np_array_positive(
        read=read, error='cannot be parsed because it should be an '
                         'np.array dtype=<float> with ascending order!',
        check=_is_sorted
    )
    np_array_greater_than_minus_one = _np_array_positive(
        read=read, error='cannot be parsed because it should be an '
                         'np.array dtype=<float> and all values >= -1!',
        check=lambda x: (x >= -1).all()
    )
    np_array_bool = _np_array(dtype=bool, read=read)
    np_array_int = _np_array(dtype=int, read=read)
    _bool = _type(type=bool, read=read)
    function = _function(read=read)
    tuplefloat2 = _type(
        type=And(Use(tuple), (_type(float),)),
        length=2,
        read=read
    )
    tuplefloat = _type(type=And(Use(tuple), (_type(float),)), read=read)
    dictstrdict = _dict(format={str: dict}, read=read)
    ordictstrdict = _ordict(format={str: dict}, read=read)
    parameters = _parameters(read=read)
    dictstrfloat = _dict(format={str: Use(float)}, read=read)
    dictarray = _dict(format={str: np_array}, read=read)
    tyre_code = _tyre_code(read=read)
    tyre_dimensions = _tyre_dimensions(read=read)

    schema = {
        _compare_str('CVT'): cvt,
        _compare_str('CMV'): cmv,
        _compare_str('CMV_Cold_Hot'): gsch,
        _compare_str('DTGS'): dtc,
        _compare_str('GSPV'): gspv,
        _compare_str('GSPV_Cold_Hot'): gsch,
        _compare_str('MVL'): _mvl(read=read),
        'engine_n_cylinders': positive_int,
        'lock_up_tc_limits': tuplefloat2,
        _convert_str(
            'ki_factor', 'ki_multiplicative'
        ): greater_than_one,
        'ki_additive': positive,
        'tyre_dimensions': tyre_dimensions,
        'tyre_code': tyre_code,
        'wltp_base_model': _dict(format=dict, read=read),
        'fuel_type': _select(types=(
            'gasoline', 'diesel', 'LPG', 'NG', 'ethanol', 'biodiesel',
            'methanol', 'propane'), read=read),
        'obd_fuel_type_code': positive_int,
        'vehicle_category': _select(types='ABCDEFSMJ', read=read),
        'vehicle_body': _select(types=(
            'cabriolet', 'sedan', 'hatchback', 'stationwagon', 'suv/crossover',
            'mpv', 'coupé', 'bus', 'bestelwagen', 'pick-up'
        ), read=read),
        'tyre_class': _select(types=('C1', 'C2', 'C3'), read=read),
        'tyre_category': _select(types='ABCDEFG', read=read),
        'engine_fuel_lower_heating_value': positive,
        'fuel_carbon_content': positive,
        'engine_capacity': positive,
        'engine_stroke': positive,
        'engine_max_power': positive,
        _convert_str(
            'engine_max_speed_at_max_power', 'engine_speed_at_max_power'
        ): positive,
        'engine_max_speed': positive,
        'engine_max_torque': positive,
        'idle_engine_speed_median': positive,
        'engine_idle_fuel_consumption': greater_than_zero,
        'final_drive_ratio': positive,
        'r_dynamic': positive,
        'wltp_class': _select(types=('class1', 'class2', 'class3a', 'class3b'),
                              read=read),
        'downscale_phases': tuplefloat,
        'gear_box_type': _select(types=('manual', 'automatic', 'cvt'),
                                 read=read),
        'ignition_type': _select(types=('positive', 'compression'), read=read),
        'start_stop_activation_time': positive,
        'alternator_nominal_voltage': positive,
        'battery_capacity': positive,
        'state_of_charge_balance': limits,
        'state_of_charge_balance_window': limits,
        'initial_state_of_charge ': limits,
        'idle_engine_speed_std': positive,
        'alternator_nominal_power': positive,
        'alternator_efficiency': _limits(limits=(0, 1), read=read),
        'time_cold_hot_transition': positive,
        'co2_params': dictstrfloat,
        'willans_factors': dictstrfloat,
        'phases_willans_factors': _type(
            type=And(Use(tuple), (dictstrfloat,)), read=read),
        'optimal_efficiency': dictarray,
        'velocity_speed_ratios': index_dict,
        'gear_box_ratios': index_dict,
        'final_drive_ratios': index_dict,
        'speed_velocity_ratios': index_dict,
        'full_load_speeds': np_array_sorted,
        'full_load_torques': np_array,
        'full_load_powers': np_array,

        'vehicle_mass': positive,
        'f0_uncorrected': positive,
        'f2': positive,
        'f0': positive,
        'correct_f0': _bool,

        'co2_emission_low': positive,
        'co2_emission_medium': positive,
        'co2_emission_high': positive,
        'co2_emission_extra_high': positive,

        _compare_str('co2_emission_UDC'): positive,
        _compare_str('co2_emission_EUDC'): positive,
        'co2_emission_value': positive,
        'declared_co2_emission_value': positive,
        'n_dyno_axes': positive_int,
        'n_wheel_drive': positive_int,
        'rcb_correction': _bool,
        'speed_distance_correction': _bool,
        'engine_is_turbo': _bool,
        'has_start_stop': _bool,
        'has_gear_box_thermal_management': _bool,
        'has_energy_recuperation': _bool,
        'use_basic_start_stop': _bool,
        'is_hybrid': _bool,
        'has_roof_box': _bool,
        'has_periodically_regenerating_systems': _bool,
        'engine_has_variable_valve_actuation': _bool,
        'has_thermal_management': _bool,
        'engine_has_direct_injection': _bool,
        'has_lean_burn': _bool,
        'engine_has_cylinder_deactivation': _bool,
        'has_exhausted_gas_recirculation': _bool,
        'has_particle_filter': _bool,
        'has_selective_catalytic_reduction': _bool,
        'has_nox_storage_catalyst': _bool,
        'has_torque_converter': _bool,
        'is_cycle_hot': _bool,
        'use_dt_gear_shifting': _bool,
        _convert_str('eco_mode', 'fuel_saving_at_strategy'): _bool,
        'correct_start_stop_with_gears': _bool,
        'enable_phases_willans': _bool,
        'enable_willans': _bool,

        'alternator_charging_currents': tuplefloat2,
        'alternator_current_model': function,
        'alternator_status_model': function,
        'clutch_model': function,
        'co2_emissions_model': function,
        'co2_error_function_on_emissions': function,
        'co2_error_function_on_phases': function,
        'cold_start_speed_model': cssm,
        'clutch_window': tuplefloat2,
        'co2_params_calibrated': parameters,
        'co2_params_initial_guess': parameters,
        'cycle_type': string,
        'cycle_name': string,
        'specific_gear_shifting': string,
        'calibration_status': _type(type=And(Use(list),
                                             [(bool, Or(parameters, None))]),
                                    length=4,
                                    read=read),
        'electric_load': tuplefloat2,
        'engine_thermostat_temperature_window': tuplefloat2,
        'engine_temperature_regression_model': function,
        'electrics_prediction_model': function,
        'engine_prediction_model': function,
        'gear_box_prediction_model': function,
        'final_drive_prediction_model': function,
        'wheels_prediction_model': function,
        'engine_type': string,
        'full_load_curve': function,
        'fmep_model': function,
        'gear_box_efficiency_constants': dictstrdict,
        'gear_box_efficiency_parameters_cold_hot': dictstrdict,
        'scores': dictstrdict,
        'param_selections': dictstrdict,
        'model_selections': dictstrdict,
        'score_by_model': dictstrdict,
        'at_scores': ordictstrdict,

        'fuel_density': positive,
        'idle_engine_speed': tuplefloat2,
        'k1': positive_int,
        'k2': positive_int,
        'k5': positive_int,
        'max_gear': positive_int,

        'road_loads': _type(type=And(Use(tuple), (_type(float),)),
                            length=3,
                            read=read),
        'start_stop_model': function,
        'gear_box_temperature_references': tuplefloat2,
        'torque_converter_model': function,
        'phases_co2_emissions': tuplefloat,
        'bag_phases': _bag_phases(read=read),
        'phases_integration_times':
            _type(type=And(Use(tuple), (And(Use(tuple), (_type(float),)),)),
                  read=read),
        'active_cylinder_ratios': tuplefloat,
        'extended_phases_co2_emissions': tuplefloat,
        'extended_phases_integration_times':
            _type(type=And(Use(tuple), (And(Use(tuple), (_type(float),)),)),
                  read=read),
        'extended_integration_times': tuplefloat,
        'phases_fuel_consumptions': tuplefloat,
        'co2_rescaling_scores': tuplefloat,
        'accelerations': np_array,
        'alternator_currents': np_array,
        'active_cylinders': np_array,
        'alternator_powers_demand': np_array,
        'alternator_statuses': np_array_int,
        'auxiliaries_power_losses': np_array,
        'auxiliaries_torque_loss': tuplefloat,
        'auxiliaries_torque_losses': np_array,
        'battery_currents': np_array,
        'clutch_tc_powers': np_array,
        'clutch_tc_speeds_delta': np_array,
        'co2_emissions': np_array,
        'cold_start_speeds_delta': np_array,
        'engine_coolant_temperatures': np_array,
        'engine_powers_out': np_array,
        'engine_speeds_out': np_array,
        'engine_speeds_out_hot': np_array,
        'engine_starts': np_array_bool,
        'co2_normalization_references': np_array,
        'final_drive_powers_in': np_array,
        'final_drive_speeds_in': np_array,
        'final_drive_torques_in': np_array,
        'fuel_consumptions': np_array,
        'gear_box_efficiencies': np_array,
        'gear_box_powers_in': np_array,
        'gear_box_speeds_in': np_array,
        'gear_box_temperatures': np_array,
        'gear_box_torque_losses': np_array,
        'gear_box_torques_in': np_array,
        'gear_shifts': np_array_bool,
        'gears': np_array_int,
        'identified_co2_emissions': np_array,
        'motive_powers': np_array,
        'on_engine': np_array_bool,
        'clutch_phases': np_array_bool,
        'cold_start_speeds_phases': np_array_bool,
        'on_idle': np_array_bool,
        'state_of_charges': np_array,
        'times': np_array_sorted,
        'velocities': np_array_greater_than_minus_one,
        _compare_str('obd_velocities'): np_array_greater_than_minus_one,
        'wheel_powers': np_array,
        'wheel_speeds': np_array,
        'wheel_torques': np_array,
    }

    schema = {Optional(k): Or(Empty(), v) for k, v in schema.items()}
    schema[Optional(str)] = Or(_type(type=float, read=read), np_array)

    if not read:
        def _f(x):
            return x is sh.NONE

        schema = {k: And(v, Or(_f, Use(str))) for k, v in schema.items()}

    return Schema(schema)


vehicle_family_id_pattern = r'''
    (?:
        (IP|RL|RM|PR) - (\d{2}) - ([A-Z0-9_]{2,3}) - (\d{4}) - (\d{4})
    )
    |
    (?:
        IP - ([A-Z0-9_]{2,15}) - ([A-Z0-9_]{3}) - ([01])
    )
'''
_vehicle_family_id_regex = re.compile('(?x)^%s$' % vehicle_family_id_pattern)
invalid_vehicle_family_id_msg = (
    "Invalid VF_ID '%s'!"
    "\n  New format is 'IP-nnn-WMI-x', where nnn is (2, 15) chars "
    "of A-Z, 0-9, or underscore(_),"
    "\n  (old format 'FT-ta-WMI-yyyy-nnnn' is still acceptable)."
)


def _vehicle_family_id(error=None, **kwargs):
    def _m(s):
        if not _vehicle_family_id_regex.match(s):
            raise SchemaError(invalid_vehicle_family_id_msg % s)
        return True

    return And(_string(**kwargs), _m, error=error)


def _input_version(error=None, **kwargs):
    def _check_data_version(input_version):
        from co2mpas import __file_version__ as exp_ver
        exp_vinfo = tuple(exp_ver.split('.'))
        got_vinfo = tuple(input_version.split('.'))

        if got_vinfo[:2] == exp_vinfo[:2]:
            return True

        if got_vinfo[:1] != exp_vinfo[:1]:
            msg = "Input-file version %s is incompatible with expected %s).\n" \
                  "  More failures may happen."

        elif got_vinfo[:2] > exp_vinfo[:2]:
            msg = "Input-file version %s comes from the (incompatible) " \
                  "future (> %s)).\n  More failures may happen."
        else:
            msg = "Input-file version %s is old (< %s))." \
                  "\n  You may need to update it, to use new fields."
        log.warning(msg, input_version, exp_ver)
        return True

    return And(_string(**kwargs), _check_data_version, error=error)


@functools.lru_cache(None)
def define_flags_schema(read=True):
    """
    Define flag schema.

    :param read:
        Schema for reading?
    :type read: bool

    :return:
        Flag schema.
    :rtype: schema.Schema
    """
    string = _string(read=read)
    isfile = _file(read=read)
    isdir = _dir(read=read)
    _bool = _type(type=bool, read=read)

    schema = {
        _compare_str('input_version'): _input_version(read=read),
        _compare_str('model_conf'): isfile,
        _compare_str('encryption_keys'): string,
        _compare_str('sign_key'): string,

        _compare_str('hard_validation'): _bool,
        _compare_str('enable_selector'): _bool,
        _compare_str('declaration_mode'): _bool,
        _compare_str('only_summary'): _bool,
        _compare_str('type_approval_mode'): _bool,

        _compare_str('output_template'): isfile,
        _compare_str('output_folder'): isdir,
    }

    schema = {Optional(k): Or(Empty(), v) for k, v in schema.items()}

    if not read:
        def _f(x):
            return x is sh.NONE

        schema = {k: And(v, Or(_f, Use(str))) for k, v in schema.items()}

    return Schema(schema)


@functools.lru_cache(None)
def define_dice_schema(read=True):
    """
    Define DICE schema.

    :param read:
        Schema for reading?
    :type read: bool

    :return:
        DICE schema.
    :rtype: schema.Schema
    """
    string = _string(read=read)
    _bool = _type(type=bool, read=read)
    _float = _type(type=float, read=read)
    schema = {
        _compare_str('vehicle_family_id'): _vehicle_family_id(read=read),
        _compare_str('bifuel'): _bool,
        _compare_str('extension'): _bool,
        _compare_str('comments'): string,
        _compare_str('atct_family_correction_factor'): _float,
        _compare_str('wltp_retest'): _select(
            types=('-', 'a', 'b', 'c', 'd', 'ab', 'ac', 'ad', 'bc', 'bd', 'cd',
                   'abc', 'abd', 'abcd'), read=read),
        _compare_str('parent_vehicle_family_id'): _vehicle_family_id(read=read),
        str: Or(Use(float), object)
    }

    schema = {Optional(k): Or(Empty(), v) for k, v in schema.items()}

    if not read:
        def _f(x):
            return x is sh.NONE

        schema = {k: And(v, Or(_f, Use(str))) for k, v in schema.items()}

    return Schema(schema)