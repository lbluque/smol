"""Implementation of Semi-Grand Canonical Ensemble Classes.

These are used to run Monte Carlo sampling for fixed number of sites but
variable concentration of species.
"""

__author__ = "Luis Barroso-Luque"


from collections import Counter

import numpy as np
from monty.json import MSONable
from pymatgen.core import DummySpecies, Element, Species

from smol.cofe.space.domain import Vacancy, get_species
from smol.moca.processor.base import Processor
from smol.moca.sublattice import Sublattice

from .base import Ensemble


class SemiGrandEnsemble(Ensemble, MSONable):
    """Relative chemical potential based SemiGrand Ensemble.

    A Semi-Grand Canonical Ensemble for Monte Carlo Simulations where species
    relative chemical potentials are predefined. Note that in the SGC Ensemble
    implemented here, only the differences in chemical potentials with
    respect to a reference species on each sublattice are fixed, and not the
    absolute values. To obtain the absolute values you must calculate the
    reference chemical potential and then simply subtract it from the given
    values.

    Attributes:
        thermo_boundaries (dict):
            dict of chemical potentials.
    """

    valid_mcmc_steps = ("flip",)

    def __init__(
        self,
        processor,
        chemical_potentials,
        sublattices=None
    ):
        """Initialize MuSemiGrandEnsemble.

        Args:
            processor (Processor):
                A processor that can compute the change in a property given
                a set of flips.
            chemical_potentials (dict):
                Dictionary with species and chemical potentials.
            sublattices (list of Sublattice): optional
                List of Sublattice objects representing sites in the processor
                supercell with same site spaces.
        """
        super().__init__(
            processor,
            sublattices=sublattices
        )
        self._params = np.append(self.processor.coefs, -1.0)
        # check that species are valid
        chemical_potentials = {
            get_species(k): v for k, v in chemical_potentials.items()
        }
        # Excessive species not appeared on active sub-lattices
        # will be dropped.
        for sp in self.species:
            if sp not in chemical_potentials.keys():
                raise ValueError(
                    f"Species {sp} was not assigned a chemical "
                    " potential, a value must be provided."
                )

        # preallocate this for slight speed improvements
        self._dfeatures = np.empty(len(processor.coefs) + 1)
        self._features = np.empty(len(processor.coefs) + 1)

        self._mus = {k: v for k, v in chemical_potentials.items()
                     if k in self.species}
        self._mu_table = self._build_mu_table(self._mus)
        self.thermo_boundaries = {
            "chemical-potentials": {str(k): v for k, v in self._mus.items()}
        }

    @property
    def natural_parameters(self):
        """Get the vector of natural parameters.

        For SGC an extra -1 is added for the chemical part of the LT.
        """
        return self._params

    @property
    def species(self):
        """Species on active sublattices.

        These are species required in setting chemical potentials.
        """
        return list({sp for sublatt in self.active_sublattices
                     for sp in sublatt.site_space})

    @property
    def chemical_potentials(self):
        """Get the chemical potentials for species in system."""
        return self._mus

    @chemical_potentials.setter
    def chemical_potentials(self, value):
        """Set the chemical potentials and update table.

        If you ever split sub-lattices or change activeness of
        sub-lattices in some other way, you have to reset chemical
        potentials before using this ensemble to run MC. Otherwise
        the _mu_table is not updated, and your chemical work might
        be wrong.
        """
        for sp, count in Counter(map(get_species, value.keys())).items():
            if count > 1:
                raise ValueError(
                    f"{count} values of the chemical potential for the same "
                    f"species {sp} were provided.\n Make sure the dictionary "
                    "you are using has only string keys or only Species "
                    "objects as keys."
                )
        value = {get_species(k): v for k, v in value.items()
                 if k in self.species}
        if set(value.keys()) != set(self.species):
            raise ValueError(
                "Chemical potentials given are missing species. "
                "Values must be given for each of the following:"
                f" {self.species}"
            )
        self._mus = value
        self._mu_table = self._build_mu_table(value)
        self.thermo_boundaries = {
            "chemical-potentials": {str(k): v for k, v in self._mus.items()}
        }

    def compute_feature_vector(self, occupancy):
        """Compute the feature vector for a given occupancy.

        In the semigrand case it is the feature vector and the chemical work
        term.

        Args:
            occupancy (ndarray):
                encoded occupancy string

        Returns:
            ndarray: feature vector
        """
        self._features[:-1] = self.processor.compute_feature_vector(occupancy)
        self._features[-1] = self.compute_chemical_work(occupancy)
        return self._features

    def compute_feature_vector_change(self, occupancy, step):
        """Return the change in the feature vector from a given step.

        Args:
            occupancy (ndarray):
                encoded occupancy string.
            step (list of tuple):
                A sequence of flips given my the MCUsher.propose_step

        Returns:
            ndarray: difference in feature vector
        """
        self._dfeatures[:-1] = self.processor.compute_feature_vector_change(
            occupancy, step
        )
        self._dfeatures[-1] = sum(
            self._mu_table[f[0]][f[1]] - self._mu_table[f[0]][occupancy[f[0]]]
            for f in step
        )
        return self._dfeatures

    def compute_chemical_work(self, occupancy):
        """Compute sum of mu * N for given occupancy."""
        return sum(
            self._mu_table[site][species] for site, species in enumerate(occupancy)
        )

    def _build_mu_table(self, chemical_potentials):
        """Build an array for chemical potentials for all sites in system.

        Rows represent sites and columns species. This allows quick evaluation
        of chemical potential changes from flips. Not that the total number
        of columns will be the number of species in the largest site space. For
        smaller site spaces the values at those rows are meaningless and will
        be given values of 0. Also rows representing sites with not partial
        occupancy will have all 0 values and should never be used.
        """
        # Mu table should be built with ensemble, rather than processor data.
        # Otherwise you may get wrong species encoding if the sub-lattices are
        # split.
        num_cols = max(max(sl.encoding) for sl in self.sublattices)
        table = np.zeros((self.num_sites, num_cols))
        for sublatt in self.active_sublattices:
            ordered_pots = [chemical_potentials[sp] for sp in sublatt.site_space]
            table[sublatt.sites, sublatt.encoding] = ordered_pots
        return table

    def as_dict(self):
        """Get Json-serialization dict representation.

        Returns:
            MSONable dict
        """
        d = super().as_dict()
        d["chemical_potentials"] = tuple(
            (s.as_dict(), c) for s, c in self.chemical_potentials.items()
        )
        return d

    @classmethod
    def from_dict(cls, d):
        """Instantiate a MuSemiGrandEnsemble from dict representation.

        Returns:
            CanonicalEnsemble
        """
        chemical_potentials = {}
        for sp, c in d["chemical_potentials"]:
            if "oxidation_state" in sp and Element.is_valid_symbol(sp["element"]):
                sp = Species.from_dict(sp)
            elif "oxidation_state" in sp:
                if sp["@class"] == "Vacancy":
                    sp = Vacancy.from_dict(sp)
                else:
                    sp = DummySpecies.from_dict(sp)
            else:
                sp = Element(sp["element"])
            chemical_potentials[sp] = c
        return cls(
            Processor.from_dict(d["processor"]),
            chemical_potentials=chemical_potentials,
            sublattices=[Sublattice.from_dict(s) for s in d["sublattices"]]
        )
