#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# Copyright 2015-2019 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl
"""
It contains classes and functions of general utility.
"""
import schedula as sh
import statistics
import numpy as np


# noinspection PyMissingOrEmptyDocstring
class Constants(dict):
    def load(self, file, **kw):
        import yaml
        with open(file, 'rb') as f:
            self.from_dict(yaml.load(f, **kw))
        return self

    def dump(self, file, default_flow_style=False, **kw):
        import yaml
        with open(file, 'w') as f:
            yaml.dump(
                self.to_dict(), f, default_flow_style=default_flow_style, **kw
            )

    def from_dict(self, d):
        for k, v in sorted(d.items()):
            if isinstance(v, dict) and '__constants__' in v:
                o = getattr(self, k, Constants())
                if isinstance(o, Constants):
                    v = o.from_dict(v['__constants__'])
                elif issubclass(o.__class__, Constants) or \
                        issubclass(o, Constants):
                    v = o().from_dict(v['__constants__'])
                if not v:
                    continue
            elif hasattr(self, k) and getattr(self, k) == v:
                continue
            setattr(self, k, v)
            self[k] = v

        return self

    def to_dict(self):
        import inspect
        s, pr = set(dir(self)) - set(dir(Constants)), {}
        for n in s.union(self.__class__.__dict__.keys()):
            if n.startswith('__'):
                continue
            v = getattr(self, n)
            if inspect.ismethod(v) or inspect.isbuiltin(v):
                continue
            if isinstance(v, Constants):
                pr[n] = {'__constants__': v.to_dict()}
            elif inspect.isclass(v) and issubclass(v, Constants):
                # noinspection PyCallByClass,PyTypeChecker
                pr[n] = {'__constants__': v.to_dict(v)}
            else:
                pr[n] = v
        return pr


class List(list):
    empty = sh.EMPTY
    dtype = None

    def __new__(cls, *args, dtype=None, **kwargs):
        obj = super(List, cls).__new__(cls, *args, **kwargs)
        obj.dtype = dtype
        return obj

    def __getitem__(self, item):
        r = super(List, self).__getitem__(item)
        if r is self.empty:
            raise IndexError('list index out of range')
        elif isinstance(item, slice):
            return self.__class__(r)
        return r

    def __setitem__(self, key, value):
        try:
            return super(List, self).__setitem__(key, value)
        except IndexError:
            self.extend([self.empty] * (key - len(self)))
            self.append(value)
            return super(List, self).__setitem__(key, value)

    def toarray(self, dtype=None, *args, **kwargs):
        return np.array(self, dtype or self.dtype, *args, **kwargs)


class BaseModel:
    key_outputs = ()
    contract_outputs = ()
    types = {}

    def __init__(self, outputs=None):
        self._outputs = outputs
        self.outputs = None

    def __call__(self, *args, n=None):
        n = len(args[0]) if n is None else n
        self.set_outputs()
        for _ in self.yield_results(*args, n=n):
            pass
        self.format_results()
        return sh.selector(self.key_outputs, self.outputs, output_type='list')

    def format_results(self):
        keys = self.contract_outputs
        for k in self.key_outputs:
            v = self.outputs[k]
            if isinstance(v, List):
                self.outputs[k] = (v[:-1] if k in keys else v).toarray()

    def set_outputs(self, outputs=None):
        if outputs is None:
            outputs = {}
        outputs.update(self._outputs or {})

        for t, names in self.types.items():
            for k in names - set(outputs):
                outputs[k] = List(dtype=t)
        self.outputs = outputs

    def init_results(self, *args):
        raise NotImplementedError

    def yield_results(self, *args, n=-1):
        i, _next = 0, self.init_results(*args)

        while n != 0:
            yield _next(i)
            i += 1
            n -= 1


def argmax(values, **kws):
    """
    Returns the indices of the maximum values along an axis.

    :param values:
        Input array.
    :type values: numpy.array | list

    :return:
        Indices of the maximum values
    :rtype: numpy.ndarray
    """
    return np.argmax(np.append(values, [True]), **kws)


def mae(x, y, w=None):
    """
    Mean absolute error.

    :param x:
        Reference values.
    :type x: numpy.array

    :param y:
        Output values.
    :type y: numpy.array

    :param w:
        Weights.
    :type w: numpy.array

    :return:
        Mean absolute error.
    :rtype: float
    """
    if w is not None:
        return np.mean(np.abs(x - y) * w)
    return np.mean(np.abs(x - y))


def sliding_window(xy, dx_window):
    """
    Returns a sliding window (of width dx) over data from the iterable.

    :param xy:
        X and Y values.
    :type xy: list[(float, float) | list[float]]

    :param dx_window:
        dX window.
    :type dx_window: float

    :return:
        Data (x & y) inside the time window.
    :rtype: generator
    """

    dx = dx_window / 2
    it = iter(xy)
    v = next(it)
    window = []

    for x, y in xy:
        # window limits
        x_dn = x - dx
        x_up = x + dx

        # remove samples
        window = [w for w in window if w[0] >= x_dn]

        # add samples
        while v and v[0] <= x_up:
            window.append(v)
            try:
                v = next(it)
            except StopIteration:
                v = None

        yield window


# noinspection PyShadowingBuiltins
def median_filter(x, y, dx_window, filter=statistics.median_high):
    """
    Calculates the moving median-high of y values over a constant dx.

    :param x:
        x data.
    :type x: Iterable

    :param y:
        y data.
    :type y: Iterable

    :param dx_window:
        dx window.
    :type dx_window: float

    :param filter:
        Filter function.
    :type filter: callable

    :return:
        Moving median-high of y values over a constant dx.
    :rtype: numpy.array
    """

    xy = list(zip(x, y))
    _y = []
    add = _y.append
    for v in sliding_window(xy, dx_window):
        add(filter(list(zip(*v))[1]))
    return np.array(_y)


def get_inliers(x, n=1, med=np.median, std=np.std):
    """
    Returns the inliers data.

    :param x:
        Input data.
    :type x: Iterable

    :param n:
        Number of standard deviations.
    :type n: int

    :param med:
        Median function.
    :type med: callable, optional

    :param std:
        Standard deviation function.
    :type std: callable, optional

    :return:
         Inliers mask, median and standard deviation of inliers.
    :rtype: (numpy.array, float, float)
    """
    x = np.asarray(x)
    if not x.size:
        return np.zeros_like(x, dtype=bool), np.nan, np.nan
    m, s = med(x), std(x)
    with np.errstate(divide='ignore', invalid='ignore'):
        y = n > (np.abs(x - m) / s)
    return y, m, s


def reject_outliers(x, n=1, med=np.median, std=np.std):
    """
    Calculates the median and standard deviation of the sample rejecting the
    outliers.

    :param x:
        Input data.
    :type x: Iterable

    :param n:
        Number of standard deviations.
    :type n: int

    :param med:
        Median function.
    :type med: callable, optional

    :param std:
        Standard deviation function.
    :type std: callable, optional

    :return:
        Median and standard deviation.
    :rtype: (float, float)
    """

    y, m, s = get_inliers(x, n=n, med=med, std=std)

    if y.any():
        y = np.asarray(x)[y]

        m, s = med(y), std(y)

    return m, s


def clear_fluctuations(times, gears, dt_window):
    """
    Clears the gear identification fluctuations.

    :param times:
        Time vector.
    :type times: numpy.array

    :param gears:
        Gear vector.
    :type gears: numpy.array

    :param dt_window:
        Time window.
    :type dt_window: float

    :return:
        Gear vector corrected from fluctuations.
    :rtype: numpy.array
    """

    xy = [list(v) for v in zip(times, gears)]

    for samples in sliding_window(xy, dt_window):

        up, dn = False, False

        x, y = zip(*samples)

        for k, d in enumerate(np.diff(y)):
            if d > 0:
                up = True
            elif d < 0:
                dn = True

            if up and dn:
                m = statistics.median_high(y)
                for v in samples:
                    v[1] = m
                break

    return np.array([y[1] for y in xy])


# noinspection PyUnusedLocal
def check_first_arg(first, *args):
    """
    Check first arg is true.

    :param first:
        First arg.
    :type first: T

    :return:
        If first arg is true.
    :rtype: bool
    """
    return bool(first)


# noinspection PyUnusedLocal
def check_first_arg_false(first, *args):
    """
    Check first arg is false.

    :param first:
        First arg.
    :type first: T

    :return:
        If first arg is false.
    :rtype: bool
    """
    return not bool(first)
