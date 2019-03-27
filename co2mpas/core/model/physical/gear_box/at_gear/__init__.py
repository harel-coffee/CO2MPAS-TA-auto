# -*- coding: utf-8 -*-
#
# Copyright 2015-2019 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl
"""
Functions and a model `dsp` to model the A/T gear shifting.

Sub-Modules:

.. currentmodule:: co2mpas.core.model.physical.gear_box.at_gear

.. autosummary::
    :nosignatures:
    :toctree: at_gear/

    cmv
    cmv_ch
    core
    dtgs
    gspv
    gspv_ch
"""

import copy
import itertools
import collections
import numpy as np
import schedula as sh
from ...defaults import dfl
import co2mpas.utils as co2_utl
from .core import define_gear_filter
from .cmv import dsp as _cmv, CMV
from .cmv_ch import dsp as _cmv_ch
from .gspv import dsp as _gspv
from .gspv_ch import dsp as _gspv_ch
from .dtgs import dsp as _dtgs

dsp = sh.BlueDispatcher(
    name='Automatic gear model',
    description='Defines an omni-comprehensive gear shifting model for '
                'automatic vehicles.'
)
dsp.add_data(
    'fuel_saving_at_strategy', dfl.values.fuel_saving_at_strategy,
    description='Apply the eco-mode gear shifting?'
)
dsp.add_data('plateau_acceleration', dfl.values.plateau_acceleration)


# noinspection PyMissingOrEmptyDocstring
class MVL(CMV):
    def __init__(self, *args,
                 plateau_acceleration=dfl.values.plateau_acceleration,
                 **kwargs):
        super(MVL, self).__init__(*args, **kwargs)
        self.plateau_acceleration = plateau_acceleration

    # noinspection PyMethodOverriding,PyMethodOverriding
    def fit(self, gears, velocities, velocity_speed_ratios, idle_engine_speed,
            stop_velocity):
        self.velocity_speed_ratios = velocity_speed_ratios
        idle = idle_engine_speed
        mvl = [np.array([idle[0] - idle[1], idle[0] + idle[1]])]
        for k in range(1, int(max(gears)) + 1):
            lm, on, vsr = [], None, velocity_speed_ratios[k]

            for i, b in enumerate(itertools.chain(gears == k, [False])):
                if not b and on is not None:
                    v = velocities[on:i]
                    lm.append([min(v), max(v)])
                    on = None

                elif on is None and b:
                    on = i

            if lm:
                min_v, max_v = zip(*lm)
                lm = [sum(co2_utl.reject_outliers(min_v)), max(max_v)]
                mvl.append(np.array([max(idle[0], l / vsr) for l in lm]))
            else:
                mvl.append(mvl[-1].copy())

        mvl = [[k, tuple(v * velocity_speed_ratios[k])]
               for k, v in reversed(list(enumerate(mvl[1:], 1)))]
        mvl[0][1] = (mvl[0][1][0], dfl.INF)
        mvl.append([0, (0, mvl[-1][1][0])])

        for i, v in enumerate(mvl[1:]):
            v[1] = (v[1][0], max(v[1][1], mvl[i][1][0] + stop_velocity))

        self.clear()
        # noinspection PyTypeChecker
        self.update(collections.OrderedDict(mvl))

        return self

    # noinspection PyMethodOverriding
    def predict(self, velocity, acceleration, gear):
        if abs(acceleration) < self.plateau_acceleration:
            for k, v in self.items():
                if k <= gear:
                    break
                elif velocity > v[0]:
                    return k

        if gear:
            while velocity > self[gear][1]:
                gear += 1

        return gear


@sh.add_function(dsp, outputs=['MVL'])
def calibrate_mvl(
        gears, velocities, velocity_speed_ratios, idle_engine_speed,
        stop_velocity):
    """
    Calibrates the matrix velocity limits (upper and lower bound) [km/h].

    :param gears:
        Gear vector [-].
    :type gears: numpy.array

    :param velocities:
        Vehicle velocity [km/h].
    :type velocities: numpy.array

    :param velocity_speed_ratios:
        Constant velocity speed ratios of the gear box [km/(h*RPM)].
    :type velocity_speed_ratios: dict[int | float]

    :param idle_engine_speed:
        Engine speed idle median and std [RPM].
    :type idle_engine_speed: (float, float)

    :param stop_velocity:
        Maximum velocity to consider the vehicle stopped [km/h].
    :type stop_velocity: float

    :return:
        Matrix velocity limits (upper and lower bound) [km/h].
    :rtype: MVL
    """

    mvl = MVL().fit(gears, velocities, velocity_speed_ratios, idle_engine_speed,
                    stop_velocity)

    return mvl


dsp.add_func(
    define_gear_filter, inputs_kwargs=True, inputs_defaults=True,
    outputs=['gear_filter']
)


# noinspection PyMissingOrEmptyDocstring,PyAttributeOutsideInit,PyUnusedLocal
class CorrectGear:
    def __init__(self, velocity_speed_ratios=None, idle_engine_speed=None):
        velocity_speed_ratios = velocity_speed_ratios or {}
        self.gears = np.array(sorted(k for k in velocity_speed_ratios if k > 0))
        self.vsr = velocity_speed_ratios
        self.min_gear = velocity_speed_ratios and self.gears[0] or None
        self.idle_engine_speed = idle_engine_speed
        self.pipe = []
        self.prepare_pipe = []

    def fit_basic_correct_gear(self):
        idle = self.idle_engine_speed[0] - self.idle_engine_speed[1]
        self.idle_vel = np.array([self.vsr[k] * idle for k in self.gears])
        self.prepare_pipe.append(self.prepare_basic)

    def prepare_basic(
            self, matrix, times, velocities, accelerations, motive_powers,
            engine_coolant_temperatures):

        max_gear = np.repeat(
            np.array([self.gears], int), velocities.shape[0], 0
        )
        max_gear[velocities[:, None] < self.idle_vel] = 0
        max_gear = max_gear.max(1)
        b = velocities > 0
        max_gear[~b] = 0
        b[-1] &= accelerations[-1] > 0
        b[:-1] &= (accelerations[:-1] > 0) | (np.diff(velocities) > 0)
        min_gear = np.minimum(np.where(b, self.min_gear, 0), max_gear)
        del b

        for gears in matrix.values():
            np.clip(gears, min_gear, max_gear, gears)
        del max_gear, min_gear
        return matrix

    def basic_correct_gear(
            self, gear, i, gears, times, velocities, accelerations,
            motive_powers, engine_coolant_temperatures, matrix):
        vel, mg = velocities[i], self.min_gear
        if not vel:
            gear = 0
        elif gear > mg:
            j = np.searchsorted(self.gears, gear)
            valid = (vel >= self.idle_vel)[::-1]
            k = valid.argmax()
            gear = valid[k] and self.gears[j - k] or mg
        if not gear:
            b = accelerations[i] > 0 or velocities.take(i + 1, mode='clip')
            gear = b and mg or 0
        return gear

    def fit_correct_gear_mvl(self, mvl):
        self.mvl = mvl
        self.mvl_acc = mvl.plateau_acceleration
        self.vl_dn, self.vl_up = np.array(list(zip(*map(mvl.get, self.gears))))
        self.pipe.append(self.correct_gear_mvl)

    def prepare_mvl(
            self, matrix, times, velocities, accelerations, motive_powers,
            engine_coolant_temperatures):

        vel = velocities[:, None]
        max_gear = np.repeat(np.array([self.gears], float), vel.shape[0], 0)
        max_gear[(vel > self.vl_up) | (vel < self.vl_dn)] = np.nan
        del vel

        b = velocities.astype(bool) & (accelerations < self.mvl_acc)
        max_gear = np.nanmax(max_gear, 1)
        c = ~np.isnan(max_gear)
        b, c = c & b, c & ~b
        max_gear = max_gear[b].astype(int), max_gear[c].astype(int)
        for gears in matrix.values():
            gears[b] = max_gear[0]
            gears[c] = np.minimum(max_gear[1], gears[c])

        del max_gear, b, c

        return matrix

    # noinspection PyUnusedLocal
    def correct_gear_mvl(
            self, gear, i, gears, times, velocities, accelerations,
            motive_powers, engine_coolant_temperatures, matrix):
        vel = velocities[i]
        if abs(accelerations[i]) < self.mvl_acc:
            j = np.where(self.vl_dn < vel)[0]
            if j.shape[0]:
                g = self.gears[j.max()]
                if g > gear:
                    return g
        if gear:
            while vel > self.mvl[gear][1]:
                gear += 1
        return gear

    def fit_correct_gear_full_load(
            self, full_load_curve, max_velocity_full_load_correction):
        self.max_velocity_full_load_corr = max_velocity_full_load_correction
        self.flc = full_load_curve
        self.np_vsr = np.array(list(map(self.vsr.get, self.gears)))
        self.pipe.append(self.correct_gear_full_load)

    def prepare_full_load(
            self, matrix, times, velocities, accelerations, motive_powers,
            engine_coolant_temperatures):

        max_gear = np.repeat(
            np.array([self.gears], float), velocities.shape[0], 0
        )
        speeds = velocities[:, None] / self.np_vsr
        speeds[:, 0] = np.maximum(speeds[:, 0], self.idle_engine_speed[0])
        max_gear[self.flc(speeds) <= motive_powers[:, None]] = np.nan
        del speeds
        max_gear = np.nanmax(max_gear, 1)
        max_gear[velocities > self.max_velocity_full_load_corr] = self.gears[-1]
        b = ~np.isnan(max_gear)
        max_gear = max_gear[b].astype(int)
        for gears in matrix.values():
            gears[b] = np.clip(gears[b], 0, max_gear)
        del max_gear, b

        return matrix

    def correct_gear_full_load(
            self, gear, i, gears, times, velocities, accelerations,
            motive_powers, engine_coolant_temperatures, matrix):
        vel = velocities[i]
        if vel > self.max_velocity_full_load_corr or gear <= self.min_gear:
            return gear

        j = np.searchsorted(self.gears, gear)
        delta = (self.flc(vel / self.np_vsr) - motive_powers[i])
        valid = delta[:j + 1][::-1] >= 0
        k = valid.argmax()
        if not valid[k]:
            return self.gears[np.argmax(delta)]
        return self.gears[j - k]

    def fit_correct_driveability_rules(self, engine_speed_at_max_power):
        idle = self.idle_engine_speed[0]
        n_min_drive = idle + 0.125 * (engine_speed_at_max_power - idle)
        idle = {1: idle, 2: idle * 0.9}
        self.min_gear_vel = {
            g: idle.get(g, n_min_drive) * self.vsr[g] for g in self.gears
        }
        self.pipe.append(self.correct_driveability_rules)
        self.next_gears = []

    # noinspection PyUnresolvedReferences
    def correct_driveability_rules(
            self, gear, i, gears, times, velocities, accelerations,
            motive_powers, engine_coolant_temperatures, matrix):
        pg = gears.take(i - 1, mode='clip')  # Previous gear.
        power = motive_powers[i]  # Current power.
        t0 = times[i]
        for j, (g, t) in enumerate(self.next_gears):
            if t0 <= t:
                gear = g
                self.next_gears = self.next_gears[j:]
                break
        else:
            self.next_gears = []

        # noinspection PyShadowingNames
        def get_next(k, g):
            t0 = times[k]
            for j, v in enumerate(self.next_gears):
                if t0 <= v[0]:
                    g = v[1]
                    break
            else:
                g = matrix[g][k]
            # 4.3
            if g and motive_powers[k] < 0 and \
                    self.min_gear_vel[g] > velocities[k]:
                return 0
            return g

        # 4.a During accelerations a gear have to last at least 2 seconds.
        if power > 0 or velocities.take(i + 1, mode='clip') > velocities[i]:
            j = np.searchsorted(times, times[i] - 2)
            gear = min(gear, pg + int((gears[j:i] == pg).all()))

        # 4.b
        if gear > 1 and power > 0:
            if pg < gear or motive_powers.take(i - 1, mode='clip') <= 0:
                g = gear
                t0 = times[i] + 10
                j = i + 1
                gen = enumerate(zip(times[j:], motive_powers[j:]), j)
                while True:
                    try:
                        k, (t, p) = next(gen)
                    except StopIteration:
                        break
                    if p <= 0:
                        break
                    g = get_next(k, g)
                    if g < gear:
                        if t <= t0:
                            gear = g
                        else:
                            t0 += 10
                            while True:
                                try:
                                    k, (t, p) = next(gen)
                                except StopIteration:
                                    break
                                if t > t0 or p <= 0:
                                    break
                                g = get_next(k, g)
                                if g < gear:
                                    gear = g
                                    break
                        break

            if pg > gear:
                g = gear
                t0 = times[i] + 10
                j = i + 1
                gen = enumerate(zip(times[j:], motive_powers[j:]), j)
                while True:
                    try:
                        k, (t, p) = next(gen)
                    except StopIteration:
                        break
                    g = get_next(k, g)
                    if g < pg:
                        break
                    if t > t0 or p <= 0:
                        gear = pg
                        break

        # 4.c, 4.d
        if pg and pg < gear:
            if power < 0 or motive_powers.take(i + 1, mode='clip') < 0:  # 4.d
                gear = pg
            else:  # 4.c
                g = gear
                t0 = times[i] + 5.01
                j = i + 1
                gen = enumerate(times[j:], j)
                while True:
                    try:
                        k, t = next(gen)
                    except StopIteration:
                        break
                    if t > t0:
                        break
                    g = get_next(k, g)
                    if g < gear:
                        gear = max(pg, g)
                        break
        # 4.e
        if gear == 1 and pg == 2 and power < 0:
            for k, p in enumerate(motive_powers[i + 1:], i + 1):
                if p >= 0:
                    if velocities[k] <= 1:
                        gear = 2
                    break

        # 4.e
        if gear and power < 0 and self.min_gear_vel[gear] > velocities[i]:
            gear = 0

        # 4.f
        if gear and power < 0 and pg > gear:
            j, g = i - 1, gear
            t0 = times.take(j, mode='clip')
            if (gears[np.searchsorted(times[:j], t0 - 3):j] == pg).all():
                t1, t2, t3 = times[i], t0 + 2, t0 + 5
                flag = gear, t1 + 3
                j = i + 1
                gen = enumerate(zip(times[j:], motive_powers[j:]), j)
                for k, (t, p) in gen:
                    if not (p <= 0 or t <= t2):
                        break
                    g = get_next(k, g)
                    if g != flag[0]:
                        flag = g, t + 3

                    if t >= flag[1]:
                        if flag[0] < gear:
                            gear = 0
                            self.next_gears = [(0, t0 + 1), (flag[0], t0 + 2)]
                        break
                    elif t > t3:
                        break
                    elif t > t2 and 2 <= pg - g <= 3:
                        gear = 0
                        self.next_gears = [(0, t0 + 1), (flag[0], t0 + 2)]
                        break

                if not gear and pg > flag[0] + 1:
                    v, g0, r = None, g, flag[0] - 1
                    t1, t2 = t0 + 2, t0 + 5
                    for k, (t, p) in gen:
                        g = get_next(k, g)
                        if p > 0 or g > g0:
                            break
                        g0 = g
                        if v is None and t >= t1:
                            v = matrix[g][k] - g <= 2
                        if t >= t2:
                            if g <= r:
                                if v:
                                    self.next_gears = [(0, t0 + 1), (r, t0 + 4)]
                                else:
                                    self.next_gears = [(0, t0 + 2), (g, t2)]
                            break

        # 4.f
        if power < 0 and gear and (pg > gear or pg == 0):
            j, g0 = i + 1, gear
            t0 = times.take(i - 1, mode='clip') + 2
            gen = enumerate(zip(times[j:], motive_powers[j:]), j)
            for k, (t, p) in gen:
                if p > 0:
                    break
                g1 = get_next(k, g0)
                if g1 == g0 and (g0 == 1 or t <= t0):
                    g0 = g1
                    continue
                if velocities[k] < 1:
                    self.next_gears = [(0, t)]
                    gear = 0
                elif not g1 and gear == 1:
                    g0 = g1
                    continue
                break

        # 3.2
        j = i + np.searchsorted(times[i:], times[i] + 1)
        if not gear and np.diff(velocities.take([j, j + 1], mode='clip')) > 0:
            gear = self.min_gear

        return gear

    def prepare(
            self, matrix, times, velocities, accelerations, motive_powers,
            engine_coolant_temperatures):
        for f in self.prepare_pipe:
            matrix = f(
                matrix, times, velocities, accelerations, motive_powers,
                engine_coolant_temperatures
            )
        return matrix

    def __call__(self, gear, i, gears, times, velocities, accelerations,
                 motive_powers, engine_coolant_temperatures, matrix):
        for f in self.pipe:
            gear = f(
                gear, i, gears, times, velocities, accelerations, motive_powers,
                engine_coolant_temperatures, matrix
            )
        return gear


def _upgrade_gsm(gsm, velocity_speed_ratios, cycle_type):
    from .cmv import CMV
    from .gspv import GSPV
    from .dtgs import DTGS
    from .core import GSMColdHot
    if isinstance(gsm, (CMV, DTGS)):
        gsm = copy.deepcopy(gsm).convert(velocity_speed_ratios)
        if cycle_type == 'NEDC':
            if isinstance(gsm, MVL):
                par = dfl.functions.correct_constant_velocity
                gsm.correct_constant_velocity(
                    up_cns_vel=par.CON_VEL_DN_SHIFT,
                    dn_cns_vel=par.CON_VEL_UP_SHIFT
                )
            elif isinstance(gsm, CMV) and not isinstance(gsm, GSPV):
                par = dfl.functions.correct_constant_velocity
                gsm.correct_constant_velocity(
                    up_cns_vel=par.CON_VEL_UP_SHIFT,
                    up_window=par.VEL_UP_WINDOW,
                    up_delta=par.DV_UP_SHIFT, dn_cns_vel=par.CON_VEL_DN_SHIFT,
                    dn_window=par.VEL_DN_WINDOW, dn_delta=par.DV_DN_SHIFT
                )
    elif isinstance(gsm, GSMColdHot):
        for k, v in gsm.items():
            gsm[k] = _upgrade_gsm(v, velocity_speed_ratios, cycle_type)

    return gsm


def correct_gear_v0(
        cycle_type, velocity_speed_ratios, mvl, idle_engine_speed,
        full_load_curve, max_velocity_full_load_correction=float('inf'),
        plateau_acceleration=float('inf')):
    """
    Returns a function to correct the gear predicted according to
    :func:`correct_gear_mvl` and :func:`correct_gear_full_load`.

    :param cycle_type:
        Cycle type (WLTP or NEDC).
    :type cycle_type: str

    :param velocity_speed_ratios:
        Constant velocity speed ratios of the gear box [km/(h*RPM)].
    :type velocity_speed_ratios: dict[int | float]

    :param mvl:
        Matrix velocity limits (upper and lower bound) [km/h].
    :type mvl: MVL

    :param idle_engine_speed:
        Engine speed idle median and std [RPM].
    :type idle_engine_speed: (float, float)

    :param full_load_curve:
        Vehicle full load curve.
    :type full_load_curve: function

    :param max_velocity_full_load_correction:
        Maximum velocity to apply the correction due to the full load curve.
    :type max_velocity_full_load_correction: float

    :param plateau_acceleration:
        Maximum acceleration to be at constant velocity [m/s2].
    :type plateau_acceleration: float

    :return:
        A function to correct the predicted gear.
    :rtype: callable
    """

    mvl = _upgrade_gsm(mvl, velocity_speed_ratios, cycle_type)
    mvl.plateau_acceleration = plateau_acceleration

    correct_gear = CorrectGear(velocity_speed_ratios, idle_engine_speed)
    correct_gear.fit_correct_gear_mvl(mvl)
    correct_gear.fit_correct_gear_full_load(
        full_load_curve, max_velocity_full_load_correction
    )
    correct_gear.fit_basic_correct_gear()

    return correct_gear


dsp.add_data(
    data_id='max_velocity_full_load_correction',
    default_value=dfl.values.max_velocity_full_load_correction
)

dsp.add_function(
    function=sh.add_args(correct_gear_v0),
    inputs=[
        'fuel_saving_at_strategy', 'cycle_type', 'velocity_speed_ratios', 'MVL',
        'idle_engine_speed', 'full_load_curve',
        'max_velocity_full_load_correction', 'plateau_acceleration'
    ],
    outputs=['correct_gear'],
    input_domain=co2_utl.check_first_arg
)


def correct_gear_v1(
        cycle_type, velocity_speed_ratios, mvl, idle_engine_speed,
        plateau_acceleration=float('inf')):
    """
    Returns a function to correct the gear predicted according to
    :func:`correct_gear_mvl`.

    :param cycle_type:
        Cycle type (WLTP or NEDC).
    :type cycle_type: str

    :param velocity_speed_ratios:
        Constant velocity speed ratios of the gear box [km/(h*RPM)].
    :type velocity_speed_ratios: dict[int | float]

    :param mvl:
        Matrix velocity limits (upper and lower bound) [km/h].
    :type mvl: OrderedDict

    :param idle_engine_speed:
        Engine speed idle median and std [RPM].
    :type idle_engine_speed: (float, float)

    :param plateau_acceleration:
        Maximum acceleration to be at constant velocity [m/s2].
    :type plateau_acceleration: float

    :return:
        A function to correct the predicted gear.
    :rtype: callable
    """

    mvl = _upgrade_gsm(mvl, velocity_speed_ratios, cycle_type)
    mvl.plateau_acceleration = plateau_acceleration

    correct_gear = CorrectGear(velocity_speed_ratios, idle_engine_speed)
    correct_gear.fit_correct_gear_mvl(mvl)
    correct_gear.fit_basic_correct_gear()

    return correct_gear


dsp.add_function(
    function=sh.add_args(correct_gear_v1),
    inputs=[
        'fuel_saving_at_strategy', 'cycle_type', 'velocity_speed_ratios', 'MVL',
        'idle_engine_speed', 'plateau_acceleration'
    ],
    outputs=['correct_gear'],
    weight=50,
    input_domain=co2_utl.check_first_arg
)


@sh.add_function(dsp, inputs_kwargs=True, outputs=['correct_gear'], weight=50)
def correct_gear_v2(
        velocity_speed_ratios, idle_engine_speed, full_load_curve,
        max_velocity_full_load_correction=float('inf')):
    """
    Returns a function to correct the gear predicted according to
    :func:`correct_gear_full_load`.

    :param velocity_speed_ratios:
        Constant velocity speed ratios of the gear box [km/(h*RPM)].
    :type velocity_speed_ratios: dict[int | float]

    :param idle_engine_speed:
        Engine speed idle median and std [RPM].
    :type idle_engine_speed: (float, float)

    :param full_load_curve:
        Vehicle full load curve.
    :type full_load_curve: function

    :param max_velocity_full_load_correction:
        Maximum velocity to apply the correction due to the full load curve.
    :type max_velocity_full_load_correction: float

    :return:
        A function to correct the predicted gear.
    :rtype: callable
    """

    correct_gear = CorrectGear(velocity_speed_ratios, idle_engine_speed)
    correct_gear.fit_correct_gear_full_load(
        full_load_curve, max_velocity_full_load_correction
    )
    correct_gear.fit_basic_correct_gear()

    return correct_gear


@sh.add_function(dsp, outputs=['correct_gear'], weight=100)
def correct_gear_v3(velocity_speed_ratios, idle_engine_speed):
    """
    Returns a function that does not correct the gear predicted.

    :param velocity_speed_ratios:
        Constant velocity speed ratios of the gear box [km/(h*RPM)].
    :type velocity_speed_ratios: dict[int | float]

    :param idle_engine_speed:
        Engine speed idle median and std [RPM].
    :type idle_engine_speed: (float, float)

    :return:
        A function to correct the predicted gear.
    :rtype: callable
    """

    correct_gear = CorrectGear(velocity_speed_ratios, idle_engine_speed)
    correct_gear.fit_basic_correct_gear()
    return correct_gear


@sh.add_function(dsp, outputs=['specific_gear_shifting'])
def default_specific_gear_shifting():
    """
    Returns the default value of specific gear shifting [-].

    :return:
        Specific gear shifting model.
    :rtype: str
    """
    return dfl.functions.default_specific_gear_shifting.SPECIFIC_GEAR_SHIFTING


# noinspection PyMissingOrEmptyDocstring
def at_domain(method):
    # noinspection PyMissingOrEmptyDocstring
    def domain(kwargs):
        return kwargs.get('specific_gear_shifting') in ('ALL', method)

    return domain


dsp.add_dispatcher(
    dsp_id='cmv_model',
    dsp=_cmv,
    input_domain=at_domain('CMV'),
    inputs=(
        'velocity_speed_ratios', 'engine_speeds_out', 'motive_powers', 'gears',
        'correct_gear', 'stop_velocity', 'accelerations', 'CMV', 'gear_filter',
        'velocities', 'cycle_type', 'times', {'specific_gear_shifting': sh.SINK}
    ),
    outputs=('CMV', 'gears')
)

dsp.add_dispatcher(
    include_defaults=True,
    dsp_id='cmv_ch_model',
    input_domain=at_domain('CMV_Cold_Hot'),
    dsp=_cmv_ch,
    inputs=(
        'CMV_Cold_Hot', 'accelerations', 'correct_gear', 'cycle_type',
        'engine_speeds_out', 'gear_filter', 'gears', 'stop_velocity',
        'time_cold_hot_transition', 'times', 'velocities', 'motive_powers',
        'velocity_speed_ratios', {'specific_gear_shifting': sh.SINK}
    ),
    outputs=('CMV_Cold_Hot', 'gears')
)

dsp.add_data(
    'use_dt_gear_shifting', dfl.values.use_dt_gear_shifting,
    description='If to use decision tree classifiers to predict gears.'
)


# noinspection PyMissingOrEmptyDocstring
def dt_domain(method):
    # noinspection PyMissingOrEmptyDocstring
    def domain(kw):
        s = kw.get('specific_gear_shifting')
        return s == method or (kw.get('use_dt_gear_shifting') and s == 'ALL')

    return domain


dsp.add_dispatcher(
    dsp_id='dtgs_model',
    input_domain=dt_domain('DTGS'),
    dsp=_dtgs,
    inputs=(
        'velocity_speed_ratios', 'velocities', 'accelerations', 'correct_gear',
        'engine_coolant_temperatures', 'gear_filter', 'gears', 'motive_powers',
        'times', 'DTGS', {
            'specific_gear_shifting': sh.SINK, 'use_dt_gear_shifting': sh.SINK
        }
    ),
    outputs=('DTGS', 'gears')
)

dsp.add_dispatcher(
    dsp_id='gspv_model',
    dsp=_gspv,
    input_domain=at_domain('GSPV'),
    inputs=(
        'GSPV', 'accelerations', 'correct_gear', 'cycle_type', 'velocities',
        'gear_filter', 'gears', 'motive_powers', 'stop_velocity', 'times',
        'velocity_speed_ratios', {'specific_gear_shifting': sh.SINK}
    ),
    outputs=('GSPV', 'gears')
)

dsp.add_dispatcher(
    include_defaults=True,
    dsp_id='gspv_ch_model',
    dsp=_gspv_ch,
    input_domain=at_domain('GSPV_Cold_Hot'),
    inputs=(
        'GSPV_Cold_Hot', 'accelerations', 'correct_gear', 'cycle_type', 'times',
        'gear_filter', 'gears', 'motive_powers', 'stop_velocity', 'velocities',
        'time_cold_hot_transition', 'velocity_speed_ratios',
        {'specific_gear_shifting': sh.SINK}
    ),
    outputs=('GSPV_Cold_Hot', 'gears')
)