"""Implementation of MCMC transition kernel classes.

A kernel essentially is an implementation of the MCMC algorithm that is used
to generate states for sampling an MCMC chain.
"""

__author__ = "Luis Barroso-Luque"

from abc import ABC, abstractmethod
from types import SimpleNamespace
from math import log
from random import random
import numpy as np

from smol.constants import kB
from smol.utils import derived_class_factory, class_name_from_str, \
    get_subclasses
from smol.moca.sampler.mcusher import mcusher_factory, MCUsher
from smol.moca.sampler.bias import mcbias_factory, MCBias

ALL_MCUSHERS = list(get_subclasses(MCUsher).keys())
ALL_BIAS = list(get_subclasses(MCBias).keys())


class Trace(SimpleNamespace):
    """Simple Trace class.

    A Trace is a simple (nested) namespace to hold additional values to be
    recorded during MC sampling.
    """
    def __init__(self, /, **kwargs):
        if not all(isinstance(val, np.ndarray) or isinstance(val, Trace)
                   for val in kwargs.values()):
            raise TypeError(
                'Trace only supports attributes of type ndarray or other '
                'Trace instances')
        super().__init__(**kwargs)

    @property
    def field_names(self):
        """Get all field names"""
        return tuple(self.__dict__.keys())

    def __setattr__(self, key, value):
        if not (isinstance(value, np.ndarray) or isinstance(value, Trace)):
            raise TypeError(
                'Trace only supports attributes of type ndarray or other '
                'Trace instances')
        super().__setattr__(key, value)


class StepTrace(Trace):
    """StepTrace class.

    Same as the above but holds a default "delta" inner trace to hold trace
    values that represent changes from previous values, to be handled similarly
    to delta_features and delta_energy.

    An StepTrace object is set as an MCKernels attribute to record
    kernel specific values during sampling.
    """

    def __init__(self, /, **kwargs):
        delta = Trace()
        super().__init__(delta=delta, **kwargs)


class MCKernel(ABC):
    """Abtract base class for transition kernels.

    A kernel is used to implement a specific MC algorithm used to sampler
    the ensemble classes. For an illustrtive example of how to derive from this
    and write a specific sampler see the MetropolisSampler.
    """

    # Lists of valid helper classes, set these in derived kernels
    valid_mcushers = None
    valid_bias = None

    def __init__(self, ensemble, step_type, *args, bias_type=None,
                 bias_kwargs=None, **kwargs):
        """Initialize MCKernel.

        Args:
            ensemble (Ensemble):
                An Ensemble instance to obtain the feautures and parameters
                used in computing log probabilities.
            step_type (str):
                String specifying the MCMC step type.
            bias (MCBias):
                A bias instance.
            bias_kwargs (dict):
                dictionary of keyword arguments to pass to the bias
                constructor.
            args:
                positional arguments to instantiate the mcusher for the
                corresponding step size.
            kwargs:
                Keyword arguments to instantiate the mcusher for the
                corresponding step size.
        """
        self.natural_params = ensemble.natural_parameters
        self.feature_fun = ensemble.compute_feature_vector
        self.trace = StepTrace()
        self._feature_change = ensemble.compute_feature_vector_change
        self._usher, self._bias = None, None

        mcusher_name = class_name_from_str(step_type)
        self.mcusher = mcusher_factory(
            mcusher_name, ensemble.sublattices, ensemble.inactive_sublattices,
            *args, **kwargs)

        if bias_type is not None:
            bias_name = class_name_from_str(bias_type)
            self.bias = mcbias_factory(
                bias_name, ensemble.sublattices, ensemble.inactive_sublattices,
                **bias_kwargs)

    @property
    def mcusher(self):
        """Get the mcusher."""
        return self._usher

    @mcusher.setter
    def mcusher(self, usher):
        """Set the MCUsher."""
        if usher.__class__.__name__ not in self.valid_mcushers:
            raise ValueError(
                f"{type(usher)} is not a valid MCUsher for this kernel.")
        self._usher = usher

    @property
    def bias(self):
        """Get the mcusher."""
        return self._bias

    @bias.setter
    def bias(self, bias):
        """Set the MCUsher."""
        if bias.__class__.__name__ not in self.valid_bias:
            raise ValueError(
                f"{type(bias)} is not a valid MCBias for this kernel.")
        if 'bias' not in self.trace.delta.field_names:
            self.trace.delta.bias = np.empty(1)
        self._bias = bias

    def set_aux_state(self, state, *args, **kwargs):
        """Set the auxiliary state from initial or checkpoint values."""
        self._usher.set_aux_state(state, *args, **kwargs)

    @abstractmethod
    def single_step(self, occupancy):
        """Attempt an MCMC step.

        Returns the next state in the chain and if the attempted step was
        successful.

        Args:
            occupancy (ndarray):
                encoded occupancy.

        Returns:
            tuple: (acceptance, occupancy, enthalpy change, features change)
        """
        return tuple()


class ThermalKernel(MCKernel):
    """Abtract base class for transition kernels with a set temperature.

    Basically all kernels should derive from this with the exception of those
    for multicanonical sampling and related methods
    """

    def __init__(self, ensemble, step_type, temperature, *args, **kwargs):
        """Initialize ThermalKernel.

        Args:
            ensemble (Ensemble):
                An Ensemble instance to obtain the feautures and parameters
                used in computing log probabilities.
            step_type (str):
                String specifying the MCMC step type.
            temperature (float):
                Temperature at which the MCMC sampling will be carried out.
            args:
                positional arguments to instantiate the mcusher for the
                corresponding step size.
            kwargs:
                Keyword arguments to instantiate the mcusher for the
                corresponding step size.
        """
        super().__init__(ensemble, step_type, *args, **kwargs)
        self.trace.temperature = np.array([temperature])
        self.beta = 1.0 / (kB * temperature)

    @property
    def temperature(self):
        """Get the temperature of kernel."""
        return self.trace.temperature

    @temperature.setter
    def temperature(self, temperature):
        """Set the temperature and beta accordingly."""
        self.trace.temperature = np.array([temperature])
        self.beta = 1.0 / (kB * temperature)


class UniformlyRandom(MCKernel):
    """A Kernel that accepts all proposed steps.

    This kernel samples the random limit distribution where all states have the
    same probability (corresponds to an infinite temperature).
    """

    valid_mcushers = ALL_MCUSHERS
    valid_bias = ALL_BIAS

    def single_step(self, occupancy):
        """Attempt an MCMC step.

        Returns the next state in the chain and if the attempted step was
        successful.

        Args:
            occupancy (ndarray):
                encoded occupancy.

        Returns:
            tuple: (acceptance, occupancy, enthalpy change, features change)
        """
        step = self._usher.propose_step(occupancy)
        delta_features = self._feature_change(occupancy, step)
        delta_enthalpy = np.dot(self.natural_params, delta_features)
        self._usher.update_aux_state(step)
        for f in step:
            occupancy[f[0]] = f[1]

        return True, occupancy, delta_enthalpy, delta_features


class Metropolis(ThermalKernel):
    """A Metropolis-Hastings kernel.

    The classic and nothing but the classic.
    """

    valid_mcushers = ALL_MCUSHERS
    valid_bias = ALL_BIAS

    def single_step(self, occupancy):
        """Attempt an MC step.

        Returns the next state in the chain and if the attempted step was
        successful.

        Args:
            occupancy (ndarray):
                encoded occupancy.

        Returns:
            tuple: (acceptance, occupancy, features change, enthalpy change)
        """
        step = self._usher.propose_step(occupancy)
        delta_features = self._feature_change(occupancy, step)
        delta_enthalpy = np.dot(self.natural_params, delta_features)
        if self._bias is not None:
            delta_bias = self._bias.compute_bias_change(occupancy, step)
            exponent = -self.beta * delta_enthalpy + delta_bias
            self.trace.delta.bias[0] = delta_bias
            accept = True if exponent >= 0 else exponent > log(random())
        else:
            accept = (True if delta_enthalpy <= 0
                      else -self.beta * delta_enthalpy > log(random()))

        if accept:
            for f in step:
                occupancy[f[0]] = f[1]
            self._usher.update_aux_state(step)

        return accept, occupancy, delta_enthalpy, delta_features


def mckernel_factory(kernel_type, ensemble, step_type, *args, **kwargs):
    """Get a MCMC Kernel from string name.

    Args:
        kernel_type (str):
            String specifying step to instantiate.
        ensemble (Ensemble)
            An Ensemble object to create the MCMC kernel from.
        step_type (str):
            String specifying the step type (ie key to mcusher type)
        *args:
            Positional arguments passed to class constructor
        **kwargs:
            Keyword arguments passed to class constructor

    Returns:
        MCKernel: instance of derived class.
    """
    kernel_name = class_name_from_str(kernel_type)
    return derived_class_factory(kernel_name, MCKernel, ensemble, step_type,
                                 *args, **kwargs)
