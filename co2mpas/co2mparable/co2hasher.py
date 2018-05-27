# -*- coding: utf-8 -*-
#
# Copyright 2015-2017 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl
"Co2mpas-model specific conversions for co2mparable-hasher."
from co2mpas.model.physical.electrics import Alternator_status_model
from co2mpas.model.physical.engine import EngineModel
from co2mpas.model.physical.engine.co2_emission import IdleFuelConsumptionModel, FMEP
from co2mpas.model.physical.engine.start_stop import EngineStartStopModel,\
    StartStopModel
from co2mpas.model.physical.engine.thermal import EngineTemperatureModel
from co2mpas.model.physical.final_drive import FinalDriveModel
from co2mpas.model.physical.gear_box import GearBoxLosses, GearBoxModel
from co2mpas.model.physical.gear_box.at_gear import CorrectGear
from co2mpas.model.physical.wheels import WheelsModel
from schedula import Dispatcher, add_args
from schedula.utils.sol import Solution

import toolz.dicttoolz as dtz

from .hasher import (Hasher, _convert_partial, _convert_obj,
                     _convert_dict, _convert_meth)


def _remove_timestamp_from_plan(item):
    notstamp = lambda k: k != 'timestamp'
    item = dtz.keyfilter(notstamp, item)
    item['flag'] = dtz.keyfilter(notstamp, item['flag'])

    return item


def _convert_interp_partial_in_fmep(item):
    item = vars(item).copy()
    item['fbc'] = _convert_partial(item['fbc'])
    return item


def _convert_fmep_in_idle(item):
    item = vars(item).copy()
    item['fmep_model'] = _convert_interp_partial_in_fmep(item['fmep_model'])
    return item


def _convert_correct_gear(cg):
    item = vars(cg).copy()
    #mvl = item['mvl']
    pipe = item['pipe']
    ppipe = item['prepare_pipe']
    del item['prepare_pipe']
    return (*_convert_dict(item), *pipe, *ppipe) #+ \
        #_convert_obj(mvl)


class Co2Hasher(Hasher):
    #: Map
    #:    {<funame}: (<xarg1>, ...)}
    #: A `None` value exclude the whole function.
    funcs_to_exclude = {
        #'get_cache_fpath': None,
        'default_start_time': None,
        'default_timestamp': None,
        'get_cache_fpath': None,
        'parse_excel_file': None,
        'cache_parsed_data': None,
        'get_template_file_name': None,
        'write_outputs': None,
        'write_to_excel': None,
        'bypass': None,
        '': None,
    }

    funs_to_reset = {
    }

    args_to_skip = {
        '_',  # make it a set, even when items below missing
        'output_folder',
        'output_template',
        'vehicle_name',
        'input_file_name',
        'overwrite_cache',
        'output_file_name',
        'timestamp',
        'start_time',
        'output_file_name',     # contains timestamp
        'excel',
        'name',

        'gear_filter',          # a function
        'tau_function',         # a `lognorm` native func with 1 input
    }

    args_to_print = {
        '_',
        'error_function_on_emissions',
        'correct_gear',
    }

    args_to_convert = {
        'base_data': _remove_timestamp_from_plan,
        'plan_data': _remove_timestamp_from_plan,
        'correct_gear': _convert_obj,
        'gear_box_loss_model': _convert_obj,
        'idle_fuel_consumption_model': _convert_fmep_in_idle,
        'fmep_model': _convert_interp_partial_in_fmep,
        'correct_gear': _convert_correct_gear,
    }

    #: Converts them through the standard :func:`_convert_obj()`.
    classes_to_convert = (
        Dispatcher, add_args,
        GearBoxLosses,
        GearBoxModel,
        IdleFuelConsumptionModel,
        FMEP,
        WheelsModel,
        FinalDriveModel,
        EngineTemperatureModel,
        EngineStartStopModel,
        StartStopModel,
        CorrectGear,
        Alternator_status_model,
        EngineModel,
    )

    classes_to_skip = {
        Solution
    }
