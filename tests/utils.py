"""
A few testing utilities that may be useful to just import and run.
Some of these are borrowed from pymatgen test scripts.
"""

import json
import random
from itertools import chain

import numpy as np
import numpy.testing as npt
from monty.json import MontyDecoder, MSONable
from pymatgen.core import Composition, Element

from smol.cofe.space.domain import Vacancy


def assert_table_set_equal(a1, a2):
    a1 = np.array(a1)
    a2 = np.array(a2)
    assert len(a1.shape) == 2
    assert a1.shape == a2.shape
    a1_set = np.concatenate((a1, -a1), axis=0)
    a2_set = np.concatenate((a2, -a2), axis=0)
    a1_set = np.array(sorted([tuple(r) for r in a1_set]))
    a2_set = np.array(sorted([tuple(r) for r in a2_set]))
    npt.assert_array_equal(a1_set, a2_set)


def assert_msonable(obj, test_if_subclass=True):
    """
    Tests if obj is MSONable and tries to verify whether the contract is
    fulfilled.
    By default, the method tests whether obj is an instance of MSONable.
    This check can be deactivated by setting test_if_subclass to False.
    """
    if test_if_subclass:
        assert isinstance(obj, MSONable)
    assert obj.as_dict() == obj.__class__.from_dict(obj.as_dict()).as_dict()
    _ = json.loads(obj.to_json(), cls=MontyDecoder)


def gen_random_occupancy(sublattices):
    """Generate a random encoded occupancy according to a list of sublattices.

    Args:
        sublattices (Sequence of Sublattice):
            A sequence of sublattices

    Returns:
        ndarray: encoded occupancy
    """
    num_sites = sum(len(sl.sites) for sl in sublattices)
    rand_occu = np.zeros(num_sites, dtype=int)
    for sublatt in sublattices:
        rand_occu[sublatt.sites] = np.random.choice(
            sublatt.encoding, size=len(sublatt.sites), replace=True
        )
    return rand_occu


def gen_random_neutral_occupancy(sublattices, lam=10):
    """Generate a random encoded occupancy according to a list of sublattices.

    Args:
        sublattices (Sequence of Sublattice):
            A sequence of sublattices

    Returns:
        ndarray: encoded occupancy
    """

    def get_charge(sp):
        if isinstance(sp, (Element, Vacancy)):
            return 0
        else:
            return sp.oxi_state

    def charge(occu, sublattices):
        charge = 0
        for sl in sublattices:
            for site in sl.sites:
                sp_id = sl.encoding.tolist().index(occu[site])
                charge += get_charge(sl.species[sp_id])
        return charge

    def flip(occu, sublattices, lam=10):
        actives = [s for s in sublattices if s.is_active]
        sl = random.choice(actives)
        site = random.choice(sl.sites)
        code = random.choice(list(set(sl.encoding) - {occu[site]}))
        occu_next = occu.copy()
        occu_next[site] = code
        C = charge(occu, sublattices)
        C_next = charge(occu_next, sublattices)
        accept = np.log(np.random.random()) < -lam * (C_next**2 - C**2)
        if accept and C != 0:
            return occu_next.copy(), C_next
        else:
            return occu.copy(), C

    occu = gen_random_occupancy(sublattices)
    for _ in range(10000):
        occu, C = flip(occu, sublattices, lam=lam)
        if C == 0:
            return occu.copy()

    raise TimeoutError("Can not generate a neutral occupancy in 10000 flips!")


def gen_random_structure(prim, size=3):
    """Generate an random ordered structure from a disordered prim

    Args:
        prim (pymatgen.Structure):
            disordered primitive structure:
        size (optional):
            size argument to structure.make_supercell

    Returns:
        ordered structure
    """
    structure = prim.copy()
    structure.make_supercell(size)
    for site in structure:
        site.species = Composition({random.choice(list(site.species.keys())): 1})
    return structure


def gen_fake_training_data(prim_structure, n=10):
    """Generate a fake structure, energy training set."""

    training_data = []
    for energy in np.random.random(n):
        struct = gen_random_structure(prim_structure, size=np.random.randint(2, 6))
        energy *= -len(struct)
        training_data.append((struct, energy))
    return training_data
