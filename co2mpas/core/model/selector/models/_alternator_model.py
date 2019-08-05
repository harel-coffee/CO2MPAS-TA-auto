# -*- coding: utf-8 -*-
#
# Copyright 2015-2019 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl
"""
Functions and constants to define the alternator_model selector.
"""
import schedula as sh
import co2mpas.utils as co2_utl
from ._core import define_sub_model, _accuracy_score
from ...physical.electrics import dsp as _electrics

#: Model name.
name = 'alternator_model'

#: Parameters that constitute the model.
models = [
    'alternator_status_model', 'electric_load', 'max_battery_charging_current',
    'alternator_current_model', 'start_demand', 'alternator_nominal_power',
    'alternator_initialization_time', 'alternator_nominal_voltage',
    'alternator_efficiency'
]

#: Inputs required to run the model.
inputs = [
    'battery_capacity', 'alternator_nominal_voltage', 'clutch_tc_powers',
    'initial_service_battery_state_of_charges', 'has_energy_recuperation',
    'times', 'on_engine', 'engine_starts', 'accelerations'
]

#: Relevant outputs of the model.
outputs = [
    'alternator_currents', 'service_battery_currents',
    'service_battery_state_of_charges', 'alternator_statuses'
]
#: Targets to compare the outputs of the model.
targets = outputs

#: Weights coefficients to compute the model score.
weights = sh.map_list(targets, 1, 1, 0, 0)

#: Metrics to compare outputs with targets.
metrics = sh.map_list(targets, *([co2_utl.mae] * 3 + [_accuracy_score]))

#: Upper score limits to raise the warnings.
up_limit = dict.fromkeys(
    ('alternator_currents', 'service_battery_currents'), 60
)

#: Prediction model.
# noinspection PyProtectedMember
dsp = sh.Blueprint(_electrics, inputs, outputs, models)._set_cls(
    define_sub_model
)