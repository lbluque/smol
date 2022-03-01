"""Implementation of ClusterSubspace and related PottsSubspace classes.

The ClusterSubspace class is the workhorse for generating the objects and
information necessary for a cluster expansion.

The PottsSubspace class is an (experimental) class that is similar, but
diverges from the CE mathematic formalism.
"""

from copy import deepcopy
from importlib import import_module
import warnings
import numpy as np
from monty.json import MSONable
from pymatgen.core import Structure, PeriodicSite
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer, SymmOp
from pymatgen.analysis.structure_matcher import \
    StructureMatcher, OrderDisorderElementComparator
from pymatgen.util.coord import is_coord_subset, is_coord_subset_pbc, \
    lattice_points_in_supercell, coord_list_mapping_pbc
from smol.cofe.space import Orbit, basis_factory, get_site_spaces, \
    get_allowed_species, Vacancy
from smol.cofe.space.basis import IndicatorBasis
from smol.exceptions import SymmetryError, StructureMatchError, \
    SYMMETRY_ERROR_MESSAGE
from smol.cofe.space.constants import SITE_TOL
from src.mc_utils import corr_from_occupancy


__author__ = "Luis Barroso-Luque, William Davidson Richards"


class ClusterSubspace(MSONable):
    """ClusterSubspace represents a subspace of functions of configuration.

    A :class:`ClusterSubspace` is the main work horse used in constructing a
    cluster expansion. It is necessary to define the terms to be included in a
    cluster expansion. A cluster subspace holds a finite set of orbits that
    contain symmetrically equivalent clusters. The orbits also contain the set
    of orbit basis functions (also known as correlation functions) that
    represent the terms in the cluster expansion. Taken together the set of all
    orbit functions for all orbits included span a subspace of the total
    function space over the configurational space of a given crystal structure
    system.

    The :class:`ClusterSubspace` also has methods to match fitting structures
    and determine site mappings for supercells of different sizes in order to
    compute correlation vectors (i.e. evaluate the orbit functions for a given
    structure).

    Holds a structure, its expansion structure and a list of Orbits.
    This class defines the cluster subspace over which to fit a cluster
    expansion: This sets the orbits (groups of clusters) and the site basis
    functions that are to be considered in the fit.

    You probably want to generate from :code:`ClusterSubspace.from_cutoffs`,
    which will auto-generate the orbits from diameter cutoffs.

    Attributes:
        symops (list of SymmOp):
            Symmetry operations of structure.
        num_corr_functions (int):
            Total number of correlation functions (orbit basis functions)
            included in the subspace.
        num_orbits (int):
            Total number of crystallographic orbits included in the subspace.
            This includes the empty orbit.
        num_clusters (int):
            Total number of clusters in the primitive cell that are included
            in the subspace.
    """

    def __init__(self, structure, expansion_structure, symops, orbits,
                 supercell_matcher=None, site_matcher=None, **matcher_kwargs):
        """Initialize a ClusterSubspace.

        You rarely will need to create a ClusterSubspace using the main
        constructor.
        Look at the class method :code:`from_cutoffs` for the "better" way to
        do instantiate a ClusterSubspace.

        Args:
            structure (Structure):
                Structure to define the cluster space. Typically the primitive
                cell. Includes all species regardless of partial occupation.
            expansion_structure (Structure):
                Structure including only sites that will be included in the
                Cluster space. (only those with partial occupancy)
            symops (list of Symmop):
                list of Symmops for the given structure.
            orbits (dict): {size: list of Orbits}
                Dictionary with size (number of sites) as keys and list of
                Orbits as values.
            supercell_matcher (StructureMatcher): optional
                A StructureMatcher class to be used to find supercell matrices
                relating the prim structure to other structures. If you pass
                this directly you should know how to set the matcher up, other
                wise matching your relaxed structures can fail, alot.
            site_matcher (StructureMatcher): optional
                A StructureMatcher class to be used to find site mappings
                relating the sites of a given structure to an appropriate
                supercell of the prim structure . If you pass this directly you
                should know how to set the matcher up other wise matching your
                relaxed structures can fail, alot.
            matcher_kwargs:
                ltol, stol, angle_tol, supercell_size: parameters to pass
                through to the StructureMatchers. Structures that don't match
                to the primitive cell under these tolerances won't be included
                in the expansion. Easiest option for supercell_size is usually
                to use a species that has a constant amount per formula unit.
                See pymatgen documentation of :class:`StructureMatcher` for
                more details.
        """
        # keep as private attributes
        self._structure = structure
        self._exp_structure = expansion_structure

        self.symops = symops  # should we even keep this as an attribute?
        self.num_corr_functions = None  # set automattically when assigning ids
        self.num_orbits = None  # same as above
        self.num_clusters = None  # same as above

        # Test that all the found symmetry operations map back to the input
        # structure otherwise you can get weird subset/superset bugs.
        fc = self._structure.frac_coords
        for op in self.symops:
            if not is_coord_subset_pbc(op.operate_multi(fc), fc, SITE_TOL):
                raise SymmetryError(SYMMETRY_ERROR_MESSAGE)

        # This structure matcher is used to determine if a given (supercell)
        # structure matches the prim structure by retrieving the matrix
        # relating them. Only the "get_supercell_matrix" method is used.
        if supercell_matcher is None:
            sc_comparator = OrderDisorderElementComparator()
            self._sc_matcher = StructureMatcher(primitive_cell=False,
                                                attempt_supercell=True,
                                                allow_subset=True,
                                                comparator=sc_comparator,
                                                scale=True,
                                                **matcher_kwargs)
        else:
            self._sc_matcher = supercell_matcher

        # This structure matcher is used to find the mapping between the sites
        # of a given supercell structure and the sites in the appropriate sized
        # supercell of the prim structure. Only "get_mapping" method is used.
        if site_matcher is None:
            site_comparator = OrderDisorderElementComparator()
            self._site_matcher = StructureMatcher(primitive_cell=False,
                                                  attempt_supercell=False,
                                                  allow_subset=True,
                                                  comparator=site_comparator,
                                                  scale=True,
                                                  **matcher_kwargs)
        else:
            self._site_matcher = site_matcher

        self._orbits = orbits
        self._external_terms = []  # List will hold external terms (i.e. Ewald)

        # Dict to cache orbit index mappings, this prevents doing another
        # structure match with the _site_matcher for structures that have
        # already been matched
        self._supercell_orb_inds = {}

        # assign the cluster ids
        self._assign_orbit_ids()

    @classmethod
    def from_cutoffs(cls, structure, cutoffs, basis='indicator',
                     orthonormal=False, use_concentration=False,
                     supercell_matcher=None, site_matcher=None,
                     **matcher_kwargs):
        """Create a ClusterSubspace from diameter cutoffs.

        Creates a :class:`ClusterSubspace` with orbits of the given size and
        diameter smaller than or equal to the given value. The diameter of an
        orbit is the maximum distance between any two sites of a cluster of
        that orbit.

        The diameter of a cluster is the maximum distance between any two
        sites in the cluster.

        This is the best (and the only easy) way to create a
        :class:`ClusterSubspace`.

        Args:
            structure (Structure):
                Disordered structure to build a cluster expansion for.
                Typically the primitive cell
            cutoffs (dict):
                dict of {cluster_size: diameter cutoff}. Cutoffs should be
                strictly decreasing. Typically something like {2:5, 3:4}.
                Empty and singlet orbits are always included.
                To obtain a subspace with only an empty and singlet terms use
                an empty dict {}
            basis (str):
                A string specifying the site basis functions
            orthonormal (bool):
                Whether to enforce an orthonormal basis. From the current
                available bases only the indicator basis is not orthogonal out
                of the box
            use_concentration (bool):
                If true the concentrations in the prim structure sites will be
                used to orthormalize site bases. This gives gives a cluster
                subspace centered about the prim composition.
            supercell_matcher (StructureMatcher): optional
                A StructureMatcher class to be used to find supercell matrices
                relating the prim structure to other structures. If you pass
                this directly you should know how to set the matcher up other
                wise matching your relaxed structures will fail, alot.
            site_matcher (StructureMatcher): optional
                A StructureMatcher class to be used to find site mappings
                relating the sites of a given structure to an appropriate
                supercell of the prim structure . If you pass this directly you
                should know how to set the matcher up other wise matching your
                relaxed structures will fail, alot.
            matcher_kwargs:
                ltol, stol, angle_tol, supercell_size. Parameters to pass
                through to the StructureMatchers. Structures that don't match
                to the primitive cell under these tolerances won't be included
                in the expansion. Easiest option for supercell_size is usually
                to use a species that has a constant amount per formula unit.

        Returns:
            ClusterSubspace
        """
        # get symmetry operations of prim structure.
        symops = SpacegroupAnalyzer(structure).get_symmetry_operations()
        # get the active sites (partial occupancy) to expand over.
        sites_to_expand = [site for site in structure
                           if site.species.num_atoms < 0.99
                           or len(site.species) > 1]
        expansion_structure = Structure.from_sites(sites_to_expand)
        # get orbits within given cutoffs
        orbits = cls._gen_orbits_from_cutoffs(expansion_structure,
                                              cutoffs, symops,
                                              basis, orthonormal,
                                              use_concentration)
        return cls(structure=structure,
                   expansion_structure=expansion_structure, symops=symops,
                   orbits=orbits, supercell_matcher=supercell_matcher,
                   site_matcher=site_matcher, **matcher_kwargs)

    @property
    def basis_type(self):
        """Get the type of site basis set used."""
        return self.orbits[0].basis_type

    @property
    def structure(self):
        """Get the underlying primitive structure including inactive sites."""
        return self._structure

    @property
    def expansion_structure(self):
        """Get the primitive expansion structure (excludes inactive sites)."""
        return self._exp_structure

    @property
    def cutoffs(self):
        """Return dict of orbit cluster cutoffs.

        These are "tight" cutoffs, as in the maximum diameter for each cluster
        size, which is <= the input to from_cutoffs.
        """
        return {size: max(orbit.base_cluster.diameter for orbit in orbits)
                for size, orbits in self._orbits.items() if size != 1}

    @property
    def orbits(self):
        """Return a list of all orbits sorted by size."""
        return [orbit for _, orbits
                in sorted(self._orbits.items()) for orbit in orbits]

    def iterorbits(self):
        """Yield orbits."""
        for _, orbits in sorted(self._orbits.items()):
            for orbit in orbits:
                yield orbit

    @property
    def orbits_by_size(self):
        """Get dictionary of orbits with key being the orbit size."""
        return self._orbits

    @property
    def orbit_multiplicities(self):
        """Get the crystallographic multiplicities for each orbit."""
        mults = [1] + [orb.multiplicity for orb in self.iterorbits()]
        return np.array(mults)

    @property
    def all_bit_combos(self):
        """Return flattened all bit_combos for each correlation function.

        A list of all bit_combos of length self.num_corr_functions.
        This allows to obtain a bit combo by bit id (empty cluster has None)
        """
        return [None] + [combos for orbit in self.orbits
                         for combos in orbit.bit_combos]

    @property
    def num_functions_per_orbit(self):
        """Get the number of correlation functions for each orbit.

        The list returned is of length total number of orbits, each entry is
        the total number of correlation functions assocaited with that orbit.
        """
        return np.array([len(orbit) for orbit in self.iterorbits()])

    @property
    def function_orbit_ids(self):
        """Get Orbit IDs corresponding to each correlation function.

        If the Cluster Subspace includes external terms these are not included
        in the list since they are not associated with any orbit.
        """
        func_orb_ids = [0]
        for orbit in self.iterorbits():
            func_orb_ids += len(orbit) * [orbit.id, ]
        return np.array(func_orb_ids)

    @property
    def function_inds_by_size(self):
        """Get correlation function indices by cluster sizes."""
        return {s: list(range(os[0].bit_id, os[-1].bit_id + len(os[-1])))
                for s, os in self._orbits.items()}

    @property
    def function_ordering_multiplicities(self):
        """Get array of ordering multiplicity of each correlation function.

        The length of the array returned is the total number of correlation
        functions in the subspace for all orbits. The ordering multiplicity of
        a correlation function is the number of symmetrically equivalent bit
        orderings the result in the product of the same single site functions.
        """
        mults = [1] + [mult for orb in self.orbits
                       for mult in orb.bit_combo_multiplicities]
        return np.array(mults)

    @property
    def function_total_multiplicities(self):
        """Get array of total multiplicity of each correlation function.

        The length of the array returned is the total number of correlation
        functions in the subspace for all orbits. The total multiplicity of a
        correlation function is the number of symmetrically equivalent bit
        orderings the result in the product of the same single site functions
        times the (crystallographic) multiplicity of the orbit.
        """
        return self.orbit_multiplicities[self.function_orbit_ids] * \
            self.function_ordering_multiplicities

    @property
    def basis_orthogonal(self):
        """Check if the orbit basis defined is orthogonal."""
        return all(orb.basis_orthogonal for orb in self.iterorbits())

    @property
    def basis_orthonormal(self):
        """Check if the orbit basis is orthonormal."""
        return all(orb.basis_orthonormal for orb in self.iterorbits())

    @property
    def external_terms(self):
        """Get external terms to be fitted together with the correlations.

        External terms are those represented by pair interaction Hamiltonians
        (i.e. Ewald electrostatics)
        """
        return self._external_terms

    def orbit_hierarchy(self, level=1, min_size=1):
        """Get orbit hierarchy by ids.

        The empty/constant cluster index 0 is technically a suborbit of all
        orbits, but is not added to the hierarchy entries.

        Args:
            level (int):
            min_size (int):

        Returns:
            list of list: each element of the inner lists is the orbit id for
            all suborbits corresponding to the orbit at the given outer list
            index.
        """
        sub_ids = [
            [suborb.id for suborb in self.get_sub_orbits(
                orb.id, level=level, min_size=min_size)]
            for orb in self.orbits
        ]

        return [[], ] + sub_ids

    def function_hierarchy(self, level=1, min_size=2, invert=False):
        """Get the correlation function hierarchy.

        The function hierarchy is t

        Args:
            level (int):
            min_size (int):
            invert (bool): optional
                Default is invert=False which gives the high to low bit combo
                hierarchy. Invert= True will invert the hierarchy into low to
                high

        Returns:
            list of list: each element of the inner lists is the bit id for
            all corr functions corresponding to the corr function at the given
            outer list index.
        """
        hierarchy = [
            self.get_sub_function_ids(i, level=level, min_size=min_size)
            for i in range(self.num_corr_functions)
        ]

        if invert:
            hierarchy = invert_mapping(hierarchy)

        return hierarchy

    def orbits_from_cutoffs(self, upper, lower=0):
        """Get orbits with clusters within given diameter cutoffs (inclusive).

        Args:
            upper (float or dict):
                upper diameter for clusters to include. If a single float
                is given then that cutoff is used for all orbit sizes.
                Otherwise a dict can be used to specify the cutoff for the
                orbit cluster sizes,
                i.e. {2: cutoff_pairs, 3: cutoff_trips, ...}
            lower (float): optional
                lower diameter for clusters to include. If a single float
                is given then that cutoff is used for all orbit sizes.
                Otherwise a dict can be used to specify the cutoff for the
                orbit cluster sizes,
                i.e. {2: cutoff_pairs, 3: cutoff_trips, ...}

        Returns:
            list of Orbits
        """
        upper = upper if isinstance(upper, dict) \
            else {k: upper for k in self._orbits.keys()}
        lower = lower if isinstance(lower, dict) \
            else {k: lower for k in self._orbits.keys()}
        return [orbit for size in upper.keys() for orbit in self._orbits[size]
                if lower[size] <= orbit.base_cluster.diameter <= upper[size]]

    def function_inds_from_cutoffs(self, upper, lower=0):
        """Get indices of corr functions by cluster cutoffs.

        Args:
            upper (float or dict):
                upper diameter for clusters to include. If a single float
                is given then that cutoff is used for all orbit sizes.
                Otherwise a dict can be used to specify the cutoff for the
                orbit cluster sizes,
                i.e. {2: cutoff_pairs, 3: cutoff_trips, ...}
            lower (float): optional
                lower diameter for clusters to include. If a single float
                is given then that cutoff is used for all orbit sizes.
                Otherwise a dict can be used to specify the cutoff for the
                orbit cluster sizes,
                i.e. {2: cutoff_pairs, 3: cutoff_trips, ...}

        Returns:
            list: of corr function indices for clusters within cutoffs
        """
        orbits = self.orbits_from_cutoffs(upper, lower)
        inds = []
        for orbit in orbits:
            inds += list(range(orbit.bit_id, orbit.bit_id + len(orbit)))
        return np.array(inds)

    def add_external_term(self, term):
        """Add an external term to subspace.

        Add an external term (e.g. an Ewald term) to the cluster expansion
        terms. External term classes must be MSONable and implement a method
        to obtain a "correlation" see smol.cofe.extern for examples.

        Args:
            term (ExternalTerm):
                An instance of an external term. Currently only EwaldTerm is
                implemented.
        """
        for added_term in self.external_terms:
            if isinstance(term, type(added_term)):
                raise ValueError(
                    f"This ClusterSubspaces already has an {type(term)}.")
        self._external_terms.append(term)

    @staticmethod
    def num_prims_from_matrix(scmatrix):
        """Get number of prim structures in a supercell for a given matrix."""
        return int(round(np.abs(np.linalg.det(scmatrix))))

    def corr_from_structure(self, structure, normalized=True, scmatrix=None,
                            site_mapping=None):
        """Get correlation vector for structure.

        Returns the correlation vector for a given structure. To do this the
        correct supercell matrix of the prim needs to be found to then
        determine the mappings between sites to create the occupancy
        string and also determine the orbit mappings to evaluate the
        corresponding cluster functions.

        Args:
            structure (Structure):
                structure to compute correlation from
            normalized (bool):
                return the correlation vector normalized by the prim cell size.
                In theory correlation vectors are always normalized, but
                getting them without normalization allows to compute the
                "extensive" values.
            scmatrix (ndarray): optional
                supercell matrix relating the prim structure to the given
                structure. Passing this if it has already been matched will
                make things much quicker. You are responsible that it is
                correct.
            site_mapping (list): optional
                Site mapping as obtained by
                :code:`StructureMatcher.get_mapping`
                such that the elements of site_mapping represent the indices
                of the matching sites to the prim structure. If you pass this
                option you are fully responsible that the mappings are correct!

        Returns:
            array: correlation vector for given structure
        """
        if scmatrix is None:
            scmatrix = self.scmatrix_from_structure(structure)

        occu = self.occupancy_from_structure(structure,
                                             scmatrix=scmatrix,
                                             site_mapping=site_mapping,
                                             encode=True)
        occu = np.array(occu, dtype=int)

        # Create a list of tuples with necessary information to compute corr
        mappings = self.supercell_orbit_mappings(scmatrix)
        orbit_list = [
            (orbit.bit_id, orbit.flat_tensor_indices,
             orbit.flat_correlation_tensors, cluster_indices)
            for cluster_indices, orbit in zip(mappings, self.orbits)
        ]
        corr = corr_from_occupancy(occu, self.num_corr_functions, orbit_list)
        size = self.num_prims_from_matrix(scmatrix)

        if self.external_terms:
            supercell = self.structure.copy()
            supercell.make_supercell(scmatrix)
            extras = [term.value_from_occupancy(occu, supercell)/size
                      for term in self._external_terms]
            corr = np.concatenate([corr, *extras])

        if not normalized:
            corr *= size

        return corr

    def refine_structure(self, structure, scmatrix=None, site_mapping=None):
        """Refine a (relaxed) structure.

        Refine a (relaxed) structure to a perfect supercell structure of the
        the prim structure (aka the corresponding "unrelaxed" structure).

        Args:
            structure (Structure):
                structure to refine to a perfect multiple of the prim
            scmatrix (ndarray): optional
                supercell matrix relating the prim structure to the given
                structure. Passing this if it has already been matched will
                make things much quicker. You are responsible for correctness.
            site_mapping (list): optional
                Site mapping as obtained by StructureMatcher.get_mapping
                such that the elements of site_mapping represent the indices
                of the matching sites to the prim structure. Again you are
                responsible.

        Returns:
             Structure: The refined structure
        """
        if scmatrix is None:
            scmatrix = self.scmatrix_from_structure(structure)

        occu = self.occupancy_from_structure(structure, scmatrix=scmatrix,
                                             site_mapping=site_mapping)

        supercell_structure = self.structure.copy()
        supercell_structure.make_supercell(scmatrix)

        sites = []
        for sp, s in zip(occu, supercell_structure):
            if not isinstance(sp, Vacancy):  # skip vacancies
                site = PeriodicSite(sp, s.frac_coords,
                                    supercell_structure.lattice)
                sites.append(site)
        return Structure.from_sites(sites)

    def occupancy_from_structure(self, structure, scmatrix=None,
                                 site_mapping=None, encode=False):
        """Occupancy string for a given structure.

        Returns a list of occupancies of each site in a the structure in the
        appropriate order set implicitly by the supercell matrix that is found.

        This function is used as input to compute correlation vectors for the
        given structure.

        This function is also useful to obtain an initial occupancy for a Monte
        Carlo simulation (make sure that the same supercell matrix is being
        used here as in the instance of the processor class for the simulation.
        Although it is recommended to use the similar function in Processor
        classes.

        Args:
            structure (Structure):
                structure to obtain a occupancy string for
            scmatrix (array): optional
                Super cell matrix relating the given structure and the
                primitive structure. I you pass the supercell you fully are
                responsible that it is the correct one! This prevents running
                the _scmatcher (supercell structure matcher)
            site_mapping (list): optional
                Site mapping as obtained by StructureMatcher.get_mapping
                such that the elements of site_mapping represent the indices
                of the matching sites to the prim structure. I you pass this
                option you are fully responsible that the mappings are correct!
                This prevents running _site_matcher to get the mappings.
            encode (bool): optional
                If true the occupancy string will have the index of the species
                in the expansion structure site spaces, rather than the
                species itself.

        Returns:
            list: occupancy string for structure.
        """
        if scmatrix is None:
            scmatrix = self.scmatrix_from_structure(structure)

        supercell = self.structure.copy()
        supercell.make_supercell(scmatrix)

        if site_mapping is None:
            site_mapping = self.structure_site_mapping(supercell, structure)

        occu = []  # np.zeros(len(self.supercell_structure), dtype=np.int)

        for i, allowed_species in enumerate(get_allowed_species(supercell)):
            # rather than starting with all vacancies and looping
            # only over mapping, explicitly loop over everything to
            # catch vacancies on improper sites
            if i in site_mapping:
                sp = structure[site_mapping.index(i)].specie
            else:
                sp = Vacancy()
            if sp not in allowed_species:
                raise StructureMatchError(
                    "A site in given structure has an  unrecognized species "
                    f"{sp}.")
            if encode:
                occu.append(allowed_species.index(sp))
            else:
                occu.append(sp)
        return occu

    def scmatrix_from_structure(self, structure):
        """Get supercell matrix from a given structure.

        Obtain the supercell structure matrix to convert the prim structure
        to a supercell equivalent to given structure.

        Args:
            structure (Structure):
                A pymatgen Structure.

        Returns:
            ndarray: matrix relating given structure and prim structure.
        """
        scmatrix = self._sc_matcher.get_supercell_matrix(structure,
                                                         self.structure)
        if scmatrix is None:
            raise StructureMatchError(
                "Supercell could not be found from structure")
        if np.linalg.det(scmatrix) < 0:
            scmatrix *= -1
        return scmatrix

    def supercell_orbit_mappings(self, scmatrix):
        """Get orbit mappings for a structure from supercell of prim.

        Return the orbit mappings for a specific supercell of the prim
        structure represented by the given matrix

        Args:
            scmatrix (array):
                array relating a supercell with the primitive matrix

        Returns:
            list of tuples:
                (orbit, indices) list of tuples with orbits and the
                site indices for all equivalent orbits in a supercell obtained
                from the given matrix.
        """
        # np.arrays are not hashable and can't be used as dict keys.
        scmatrix = np.array(scmatrix)
        # so change them into a tuple of sorted tuples for unique keys.
        scm = tuple(sorted(tuple(s.tolist()) for s in scmatrix))
        indices = self._supercell_orb_inds.get(scm)

        if indices is None:
            indices = self._gen_orbit_indices(scmatrix)
            self._supercell_orb_inds[scm] = indices

        return indices

    def change_site_bases(self, new_basis, orthonormal=False):
        """Change the type of site basis used in the site basis functions.

        Args:
            new_basis (str):
                name of new basis for all site bases
            orthonormal (bool):
                option to orthonormalize all new site basis sets
        """
        for orbit in self.iterorbits():
            orbit.transform_site_bases(new_basis, orthonormal)

    def remove_orbits(self, orbit_ids):
        """Remove whole orbits by their ids.

        Removes orbits from cluster spaces. It is helpful to print a
        ClusterSubspace or ClusterExpansion to obtain orbit ids. After removing
        orbits, orbit id and orbit bit id are re-assigned.

        This is useful to prune a ClusterExpansion by removing orbits with
        small associated coefficients or ECI. Note that this will remove a full
        orbit, which for the case of sites with only two species is the same as
        removing a single correlation vector element (only one ECI). For cases
        with sites having more than 2 species allowed per site there are more
        than one orbit functions (for all the possible bit orderings) and
        removing an orbit will remove more than one element in the correlation
        vector.

        Args:
            orbit_ids (list):
                list of orbit ids to be removed
        """
        if min(orbit_ids) < 0:
            raise ValueError(
                'Index out of range. Negative inds are not allowed.')
        elif min(orbit_ids) == 0:
            raise ValueError(
                "The empty orbit can not be removed. \n If you really want to "
                "do this remove the first column in your feature matrix before"
                " fitting.")
        elif max(orbit_ids) > self.num_orbits - 1:
            raise ValueError(
                "Index out of range. "
                "Total number of orbits is: {self.num_orbits}")

        for size, orbits in self._orbits.items():
            self._orbits[size] = [orbit for orbit in orbits
                                  if orbit.id not in orbit_ids]

        self._assign_orbit_ids()  # Re-assign ids
        # Clear the cached supercell orbit mappings
        self._supercell_orb_inds = {}

    def remove_orbit_bit_combos(self, bit_ids):
        """Remove orbit bit combos by their ids.

        Removes a specific bit combo from an orbit. This allows more granular
        removal of terms involved in fitting/evaluating a cluster expansion.
        Similar to remove_orbits this is useful to prune a cluster expansion
        and actually allows to remove a single term (ie one with small
        associated coefficient/ECI).

        This procedure is perfectly well posed mathematically. The resultant
        CE is still a valid function of configurations with all the necessary
        symmetries from the underlying structure. Chemically however it is not
        obvious what it means to remove certain combinations of an n-body
        interaction term, and not the whole term itself. It would be justified
        if we allow "in group" orbit eci sparsity...which everyone in the field
        does anyway...

        Args:
            bit_ids (list):
                list of orbit bit ids to remove
        """
        empty_orbit_ids = []
        bit_ids = np.array(bit_ids, dtype=int)

        for orbit in self.iterorbits():
            first_id = orbit.bit_id
            last_id = orbit.bit_id + len(orbit)
            to_remove = bit_ids[bit_ids >= first_id]
            to_remove = to_remove[to_remove < last_id] - first_id
            if to_remove.size > 0:
                try:
                    orbit.remove_bit_combos_by_inds(to_remove)
                except RuntimeError:
                    empty_orbit_ids.append(orbit.id)
                    warnings.warn(
                        "All bit combos have been removed from orbit with id "
                        f"{orbit.id}. This orbit will be fully removed.")

        if empty_orbit_ids:
            self.remove_orbits(empty_orbit_ids)
        else:
            self._assign_orbit_ids()  # Re-assign ids

    def copy(self):
        """Deep copy of instance."""
        return deepcopy(self)

    def structure_site_mapping(self, supercell, structure):
        """Get structure site mapping.

        Returns the mapping between sites in the given structure and a prim
        supercell of the corresponding size.

        Args:
            supercell (Structure):
                Supercell of prim structure with same size as other structure.
            structure (Structure):
                Structure to obtain site mappings to supercell of prim
        Returns:
            list: site mappings of structure to supercell
        """
        mapping = self._site_matcher.get_mapping(supercell, structure)
        if mapping is None:
            raise StructureMatchError(
                "Mapping could not be found from structure.")
        return mapping.tolist()

    def get_sub_orbits(self, orbit_id, level=1, min_size=1):
        """Get sub orbits of the orbit for the corresponding orbit_id.

        Args:
            orbit_id (int):
                id of orbit to get sub orbit id for
            level (int): optional
                how many levels down to look for suborbits. If all suborbits
                are needed make level large enough or set to None.
            min_size (int): optional
                minimum size of clusters in sub orbits to include

        Returns:
            list of ints: list containing ids of suborbits
        """
        if orbit_id == 0:
            return []
        size = self.orbits[orbit_id - 1].base_cluster.size
        if level is None or level < 0 or size - level - 1 < 0:
            stop = 0
        elif min_size > size - level:
            stop = min_size - 1
        else:
            stop = size - level - 1

        search_sizes = range(size - 1, stop, -1)
        return [orbit for s in search_sizes for orbit in self._orbits[s]
                if self.orbits[orbit_id - 1].is_sub_orbit(orbit)]

    def get_sub_function_ids(self, corr_id, level=1, min_size=1):
        """Get the bit combo ids of all sub correlation functions.

        A sub correlation function of a given correlation function means that
        the sub correlation fucntion is a factor of the correlation function
        (with the additional requirement of acting over the sites in sub
        clusters of the clusters over which the given corr function acts on).

        In other works think of it an orbit of function labeled subclusters
        of a given orbit of function labeled clusters...a mouthful...

        Args:
            corr_id (int):
                id of orbit to get sub orbit id for
            level (int): optional
                how many levels down to look for suborbits. If all suborbits
                are needed make level large enough or set to None.
            min_size (int): optional
                minimum size of clusters in sub orbits to include

        Returns:
            list of ints: list containing ids of sub correlation functions
        """
        if corr_id == 0:
            return []

        orbit = self.orbits[self.function_orbit_ids[corr_id] - 1]
        bit_combo = orbit.bit_combos[corr_id - orbit.bit_id]

        sub_fun_ids = []
        for sub_orbit in self.get_sub_orbits(orbit.id, level=level,
                                             min_size=min_size):
            inds = orbit.sub_orbit_mappings(sub_orbit)
            for i, sub_bit_combo in enumerate(sub_orbit.bit_combos):
                if np.any(
                        np.all(
                            sub_bit_combo[0] == bit_combo[:, inds], axis=2)):
                    sub_fun_ids.append(sub_orbit.bit_id + i)

        return sub_fun_ids

    def _assign_orbit_ids(self):
        """Assign unique id's to orbit.

        Assign unique id's to each orbit based on all its orbit functions and
        all clusters in the prim structure that are in each orbit.
        """
        counts = (1, 1, 1)
        for key in sorted(self._orbits.keys()):
            for orbit in self._orbits[key]:
                counts = orbit.assign_ids(*counts)

        self.num_orbits = counts[0]
        self.num_corr_functions = counts[1]
        self.num_clusters = counts[2]

    @staticmethod
    def _gen_orbits_from_cutoffs(exp_struct, cutoffs, symops, basis, orthonorm,
                                 use_conc):
        """Generate orbits from diameter cutoffs.

        The diameter of a cluster is the maximum distance between any two
        sites in the cluster.

        Generates dictionary of {size: [Orbits]} given a dictionary of maximal
        cluster diameters and symmetry operations to apply (not necessarily all
        the symmetries of the expansion_structure).

        Args:
            exp_struct (Structure):
                Structure with all sites that have partial occupancy.
            cutoffs (dict):
                dict of cutoffs for cluster diameters {size: cutoff}.
                Cutoff diameters must decrease with cluster size, otherwise
                algorithm can not guarantee completeness of cluster subspace.
            symops (list of SymmOps):
                list of symmetry operations for structure
            basis (str):
                name identifying site basis set to use.
            orthonorm (bool):
                wether to ensure orthonormal basis set.
            use_conc (bool):
                If true the concentrations in the prim structure sites will be
                used as the measure to orthormalize site bases.
        Returns:
            dict: {size: list of Orbits within diameter cutoff}
        """
        site_spaces = get_site_spaces(exp_struct, include_measure=use_conc)
        site_bases = tuple(
            basis_factory(basis, site_space) for site_space in site_spaces)
        if orthonorm:
            for basis in site_bases:
                basis.orthonormalize()

        orbits = {}
        nbits = np.array([len(b) - 1 for b in site_spaces])

        # Generate singlet/point orbits
        for nbit, site, sbasis in zip(nbits, exp_struct, site_bases):
            # Coordinates of point terms must stay in [0, 1] to guarantee
            # correct math of the following algorithm.
            new_orbit = Orbit(
                [np.mod(site.frac_coords, 1)], exp_struct.lattice,
                [list(range(nbit))], [sbasis], symops)
            if new_orbit not in new_orbits:
                new_orbits.append(new_orbit)

        orbits[1] = sorted(
            new_orbits,
            key=lambda x: (np.round(x.base_cluster.diameter, 6),
                           -x.multiplicity))

        if len(cutoffs) == 0:  # return singlets only if no cutoffs provided
            return orbits

        orbits.update(
            ClusterSubspace._gen_multi_orbits(orbits[1], exp_struct, cutoffs,
                                              site_bases, nbits, symops)
        )
        return orbits

    @staticmethod
    def _gen_point_orbits(exp_struct, site_bases, nbits, symops):
        """Generate point orbits.

        Args:
            nbits (ndarray):
                array with total values for function indices per site.
            exp_struct (Structure):
                expansion structure, disordered sites only.
            site_bases (list of DiscreteBasis):
                list of site basis for each site in the expansion structure.
            symops (list of SymmOp):
                lists of symmetry operations of the underlying structure.

        Returns:
            list of Orbits:
                list of point orbits.
        """
        pt_orbits = []
        for nbit, site, sbasis in zip(nbits, exp_struct, site_bases):
            # Coordinates of point terms must stay in [0, 1] to guarantee
            # correct math of the following algorithm.
            new_orbit = Orbit(
                [np.mod(site.frac_coords, 1)], exp_struct.lattice,
                [list(range(nbit))], [sbasis], symops)
            if new_orbit not in pt_orbits:
                pt_orbits.append(new_orbit)

        pt_orbits = sorted(
            pt_orbits,
            key=lambda x: (np.round(x.base_cluster.diameter, 6),
                           -x.multiplicity))
        return pt_orbits


    @staticmethod
    def _gen_multi_orbits(point_orbits, exp_struct, cutoffs, site_bases, nbits,
                          symops):
        """Generate point orbits.

        Args:
            point_orbits (list of Orbit):
                list of point orbits.
            exp_struct (Structure):
                expansion structure, disordered sites only.
            cutoffs (dict):
                dict of cutoffs for cluster diameters {size: cutoff}.
                Cutoff diameters must decrease with cluster size, otherwise
                algorithm can not guarantee completeness of cluster subspace.
                Adding a cutoff term for point terms, ie {1: None} is useful
                to exclude point terms any other value for the cutoff will
                simply be ignored.
            site_bases (list of DiscreteBasis):
                list of site basis for each site in the expansion structure.
            nbits (ndarray):
                array with total values for function indices per site.
            symops (list of SymmOp):
                lists of symmetry operations of the underlying structure.

        Returns:
            dict:
                {size: list of Orbits within diameter cutoff}
        """
        # Vector sum of a, b, c divided by 2.
        # diameter + max_lp gives maximum possible distance from
        # [0.5, 0.5, 0.5] prim centoid to a point in all enumerable
        # clusters. Add SITE_TOL as a numerical tolerance grace.
        orbits = {1: point_orbits}
        max_lp = np.linalg.norm(exp_struct.lattice.matrix.sum(axis=0)) / 2
        max_lp += SITE_TOL
        for size, diameter in sorted(cutoffs.items()):
            new_orbits = []
            neighbors = exp_struct.get_sites_in_sphere(
                [0.5, 0.5, 0.5], diameter + max_lp, include_index=True)
            for orbit in orbits[size-1]:
                if orbit.base_cluster.diameter > diameter:
                    continue
                for neighbor in neighbors:
                    if is_coord_subset(
                            [neighbor.frac_coords], orbit.base_cluster.sites,
                            atol=SITE_TOL):
                        continue
                    new_sites = np.concatenate(
                        [orbit.base_cluster.sites, [neighbor.frac_coords]])
                    new_orbit = Orbit(
                        new_sites, exp_struct.lattice,
                        orbit.bits + [list(range(nbits[neighbor.index]))],
                        orbit.site_bases + [site_bases[neighbor.index]],
                        symops)

                    if new_orbit.base_cluster.diameter > diameter + 1e-8:
                        continue
                    elif new_orbit not in new_orbits:
                        new_orbits.append(new_orbit)

            if len(new_orbits) > 0:
                orbits[size] = sorted(
                    new_orbits,
                    key=lambda x: (np.round(x.base_cluster.diameter, 6),
                                   -x.multiplicity))
        return orbits

    def _gen_orbit_indices(self, scmatrix):
        """Find all the site indices associated with each orbit in structure.

        The structure corresponding to the given supercell matrix w.r.t prim.
        """
        supercell = self.structure.copy()
        supercell.make_supercell(scmatrix)
        prim_to_supercell = np.linalg.inv(scmatrix)
        supercell_fcoords = np.array(supercell.frac_coords)

        ts = lattice_points_in_supercell(scmatrix)
        orbit_indices = []
        for orbit in self.iterorbits():
            prim_fcoords = np.array([c.sites for c in orbit.clusters])
            fcoords = np.dot(prim_fcoords, prim_to_supercell)
            # tcoords contains all the coordinates of the symmetrically
            # equivalent clusters the indices are: [equivalent cluster
            # (primitive cell), translational image, index of site in cluster,
            # coordinate index]
            tcoords = fcoords[:, None, :, :] + ts[None, :, None, :]
            tcs = tcoords.shape
            inds = coord_list_mapping_pbc(
                tcoords.reshape((-1, 3)), supercell_fcoords,
                atol=SITE_TOL).reshape((tcs[0] * tcs[1], tcs[2]))  # noqa
            # orbit_ids holds orbit, and 2d array of index groups that
            # correspond to the orbit
            # the 2d array may have some duplicates. This is due to
            # symetrically equivalent groups being matched to the same sites
            # (eg in simply cubic all 6 nn interactions will all be [0, 0]
            # indices. This multiplicity disappears as supercell_structure size
            # increases, so I haven't implemented a more efficient method
            orbit_indices.append(inds)

        return orbit_indices

    def __eq__(self, other):
        """Check equality between cluster subspaces."""
        if not isinstance(other, ClusterSubspace):
            return False
        if other.num_corr_functions != self.num_corr_functions:
            return False
        if len(self.external_terms) != len(other.external_terms):
            return False
        if not all(isinstance(t1, type(t2)) for t1, t2 in
                   zip(other.external_terms, self.external_terms)):
            return False
        # does not check if basis functions are the same.
        return all(o1 == o2 for o1, o2 in zip(other.orbits, self.orbits))

    def __len__(self):
        """Get number of correlation functions and ext terms in subspace."""
        return self.num_corr_functions + len(self.external_terms)

    def __str__(self):
        """Convert class into pretty string for printing."""
        s = f'ClusterBasis: [Prim Composition] {self.structure.composition}\n'
        s += '    [Size] 0\n      [Orbit] id: 0  orderings: 1\n'
        for size, orbits in self._orbits.items():
            s += f'    [Size] {size}\n'
            for orbit in orbits:
                s += f'      {orbit}\n'
        return s

    @classmethod
    def from_dict(cls, d):
        """Create ClusterSubspace from an MSONable dict."""
        symops = [SymmOp.from_dict(so_d) for so_d in d['symops']]
        orbits = {int(s): [Orbit.from_dict(o) for o in v]
                  for s, v in d['orbits'].items()}
        structure = Structure.from_dict(d['structure'])
        exp_structure = Structure.from_dict(d['expansion_structure'])
        sc_matcher = StructureMatcher.from_dict(d['sc_matcher'])
        site_matcher = StructureMatcher.from_dict(d['site_matcher'])
        cs = cls(structure=structure,
                 expansion_structure=exp_structure,
                 orbits=orbits, symops=symops,
                 supercell_matcher=sc_matcher,
                 site_matcher=site_matcher)

        # attempt to recreate external terms. This can be much improved if
        # a base class is used.
        # TODO update this using instances of BasePairTerm when the time comes
        for term in d['external_terms']:
            try:
                module = import_module(term['@module'])
                term_class = getattr(module, term['@class'])
                cs.add_external_term(term_class.from_dict(term))
            except AttributeError:
                warnings.warn(
                    f"{term['@class']} was not found in {term['@module']}. You"
                    f" will need to add this yourself.", RuntimeWarning)
            except ImportError:
                warnings.warn(
                    f"Module {term['@module']} for class {term['@class']} was "
                    f"not found. You will have to add this yourself.",
                    ImportWarning)
        # re-create supercell orb inds cache
        _supercell_orb_inds = {}
        for scm, indices in d['_supercell_orb_inds']:
            scm = tuple(tuple(s) for s in scm)
            if isinstance(indices, tuple):
                warnings.warn(
                    "This ClusterSubspace was created with a previous version "
                    "of smol. Please resave it to avoid this warning.",
                    FutureWarning)
                _supercell_orb_inds[scm] = [
                    np.array(ind) for o_id, ind in indices]
            else:
                _supercell_orb_inds[scm] = [
                    np.array(ind) for ind in indices]
        cs._supercell_orb_inds = _supercell_orb_inds
        return cs

    def as_dict(self):
        """
        Json-serialization dict representation.

        Returns:
            MSONable dict
        """
        # modify cached sc orb inds so it can be serialized
        _supercell_orb_inds = [(scm, [ind.tolist() for ind
                               in orb_inds]) for scm, orb_inds
                               in self._supercell_orb_inds.items()]

        d = {'@module': self.__class__.__module__,
             '@class': self.__class__.__name__,
             'structure': self.structure.as_dict(),
             'expansion_structure': self.expansion_structure.as_dict(),
             'symops': [so.as_dict() for so in self.symops],
             'orbits': {s: [o.as_dict() for o in v]
                        for s, v in self._orbits.items()},
             'sc_matcher': self._sc_matcher.as_dict(),
             'site_matcher': self._site_matcher.as_dict(),
             'external_terms': [et.as_dict() for et in self.external_terms],
             '_supercell_orb_inds': _supercell_orb_inds}
        return d


def invert_mapping(mapping):
    """Invert a mapping table from forward to backward, vice versa.

    Args:
        mapping (list of lists):
            List of sublists, each contains integer indices, indicating
            a foward mapping from the current sublist index to the indices
            in the sublist.

    Returns:
        lists of lists: Inverted mapping table containing backward mapping.
        Same format as input.
    """
    inv_mapping = [[] for _ in range(len(mapping))]

    for i in range(len(mapping) - 1, -1, -1):
        values_list = mapping[i]
        for value in values_list:
            if not (i in inv_mapping[value]):
                inv_mapping[value].append(i)

    return inv_mapping


def get_complete_mapping(mapping):
    """Get a complete mapping from a 1 level mapping.

    If we allow transferability between mapping linkages, there would be
    a full mapping containing all linkages at all connectivity level.
    Using this function, you can get a full mapping from an incomplete
    mapping.

    NOTE: Since complete hierarchy is not very useful for actual CE fit, we
    will not include it as an attribute of ClusterSubspace.

    Args:
        mapping (list of lists):
             List of sublists, each contains integer indices, indicating
            a foward mapping from the current sublist index to the indices
            in the sublist.

    Returns:
        list of lists: Full mapping table containing forward mapping, but with
        all connectivity levels. Same format as input
    """
    all_level_mapping = deepcopy(mapping)

    for i in range(len(mapping) - 1, -1, -1):
        next_values_list = mapping[i]

        while len(next_values_list) > 0:
            for next_value in next_values_list:
                if next_value not in all_level_mapping[i]:
                    all_level_mapping[i].append(next_value)
            next_values_list_new = []
            for next_value in next_values_list:
                for nn_value in mapping[next_value]:
                    if nn_value not in next_values_list_new:
                        next_values_list_new.append(nn_value)
            next_values_list = next_values_list_new

    return all_level_mapping


class PottsSubspace(ClusterSubspace):
    """PottsSubspace represents a subspace of functions using only indicators.

    A PottsSubspace is a related model to a ClusterSubspace. The only
    difference is that the single site functions for any orbit are only made
    up of indicator functions (and there is no constant function). As such it
    is more closely related to a generalized Ising model or better yet an
    extension of the q-state Potts Model (hence the name).

    The orbit functions in a PottsSubspace represent the concentrations of
    decorations of clusters in the given orbit. Similar to a cluster subspace
    with site indicator basis functions. But in contrast, the Potts subspace
    includes the concentration of all possible decorations (minus 1).

    Although quite similar to a ClusterSubspace, there is no mathematical
    formalism guaranteeing that the orbit basis functions generated in a
    PottsSubspace are a linear independent set spanning configuration space,
    actually if all orbits up to any size (or infinite size) the corresponding
    orbit function set is an overcomplete family.

    A PottsSubspace can be created directly with a ClusterSubspace object
    by directly using the constructor and providing the approprately
    constructed orbits....
    but that is a bit more painful that just using the class method here.
    """

    def __init__(self, structure, expansion_structure, symops, orbits,
                 without_last_cluster=True, supercell_matcher=None,
                 site_matcher=None, **matcher_kwargs):
        """Initialize a PottsSubspace.

        You rarely will need to create a ClusterSubspace using the main
        constructor.
        Look at the class method :code:`from_cutoffs` for the "better" way to
        do instantiate a ClusterSubspace.

        Args:
            structure (Structure):
                Structure to define the cluster space. Typically the primitive
                cell. Includes all species regardless of partial occupation.
            expansion_structure (Structure):
                Structure including only sites that will be included in the
                Cluster space. (only those with partial occupancy)
            symops (list of Symmop):
                list of Symmops for the given structure.
            orbits (dict): {size: list of Orbits}
                Dictionary with size (number of sites) as keys and list of
                Orbits as values.
            without_last_cluster (bool): optional
                Wether last cluster labeling is removed from each orbit.
            supercell_matcher (StructureMatcher): (optional)
                A StructureMatcher class to be used to find supercell matrices
                relating the prim structure to other structures. If you pass
                this directly you should know how to set the matcher up, other
                wise matching your relaxed structures can fail, alot.
            site_matcher (StructureMatcher): (optional)
                A StructureMatcher class to be used to find site mappings
                relating the sites of a given structure to an appropriate
                supercell of the prim structure . If you pass this directly you
                should know how to set the matcher up other wise matching your
                relaxed structures can fail, alot.
            matcher_kwargs:
                ltol, stol, angle_tol, supercell_size: parameters to pass
                through to the StructureMatchers. Structures that don't match
                to the primitive cell under these tolerances won't be included
                in the expansion. Easiest option for supercell_size is usually
                to use a species that has a constant amount per formula unit.
                See pymatgen documentation of :class:`StructureMatcher` for
                more details.
        """
        self._wo_last_cluster = without_last_cluster
        super().__init__(structure, expansion_structure, symops, orbits,
                         supercell_matcher, site_matcher, **matcher_kwargs)

    @classmethod
    def from_cutoffs(cls, structure, cutoffs, remove_last_cluster=False,
                     supercell_matcher=None, site_matcher=None,
                     **matcher_kwargs):
        """Create a PottsSubspace from diameter cutoffs.

        Creates a :class:`PottsSubspace` with orbits of the given size and
        diameter smaller than or equal to the given value. The diameter of an
        orbit is the maximum distance between any two sites of a cluster of
        that orbit.

        The diameter of a cluster is the maximum distance between any two
        sites in the cluster.

        Args:
           structure (Structure):
               Disordered structure to build a cluster expansion for.
               Typically the primitive cell
           cutoffs (dict):
               dict of {cluster_size: diameter cutoff}. Cutoffs should be
               strictly decreasing. Typically something like {2:5, 3:4}.
               Empty and singlet orbits are always included.
               To obtain a subspace with only an empty and singlet terms use
               an empty dict {}
           remove_last_cluster (bool): optional
               If true will remove the last cluster labeling (decoration)
               from each orbit. Since sum of corr for all labelings = 1,
               removing the last is similar to working in concentration space.
           supercell_matcher (StructureMatcher): (optional)
               A StructureMatcher class to be used to find supercell matrices
               relating the prim structure to other structures. If you pass
               this directly you should know how to set the matcher up other
               wise matching your relaxed structures will fail, alot.
           site_matcher (StructureMatcher): (optional)
               A StructureMatcher class to be used to find site mappings
               relating the sites of a given structure to an appropriate
               supercell of the prim structure . If you pass this directly you
               should know how to set the matcher up other wise matching your
               relaxed structures will fail, alot.
           matcher_kwargs:
               ltol, stol, angle_tol, supercell_size. Parameters to pass
               through to the StructureMatchers. Structures that don't match
               to the primitive cell under these tolerances won't be included
               in the expansion. Easiest option for supercell_size is usually
               to use a species that has a constant amount per formula unit.

        Returns:
           PottsSubSpace
        """
        # get symmetry operations of prim structure.
        symops = SpacegroupAnalyzer(structure).get_symmetry_operations()
        # get the active sites (partial occupancy) to expand over.
        sites_to_expand = [site for site in structure
                           if site.species.num_atoms < 0.99
                           or len(site.species) > 1]
        expansion_structure = Structure.from_sites(sites_to_expand)
        # get orbits within given cutoffs
        orbits = cls._gen_orbits_from_cutoffs(expansion_structure, cutoffs,
                                              symops, remove_last_cluster)
        return cls(structure=structure,
                   expansion_structure=expansion_structure, symops=symops,
                   orbits=orbits, without_last_cluster=remove_last_cluster,
                   supercell_matcher=supercell_matcher,
                   site_matcher=site_matcher, **matcher_kwargs)

    @staticmethod
    def _gen_orbits_from_cutoffs(exp_struct, cutoffs, symops, remove_last):
        """Generate orbits from diameter cutoffs.

        Generates dictionary of orbits in the same way that the cluster
        subspace class does, except that the orbit functions (and corresponding
        bit combos) include all symmetrically distinct decorations/labelings
        of indicator functions for all allowed species (except 1 decoration
        for each orbit since this value is just 1 - sum of concentration of all
        other decorations

        Args:
            exp_struct (Structure):
                Structure with all sites that have partial occupancy.
            cutoffs (dict):
                dict of cutoffs for cluster diameters {size: cutoff}
            symops (list of SymmOps):
                list of symmetry operations for structure
            remove_last (bool):
                remove the last cluster labeling from each orbit.

        Returns:
            dict: {size: list of Orbits within diameter cutoff}
        """
        site_spaces = get_site_spaces(exp_struct)
        site_bases = tuple(
            IndicatorBasis(site_space) for site_space in site_spaces)
        orbits = {}
        new_orbits = []
        nbits = np.array([len(b) for b in site_spaces])

        # Generate singlet/point orbits
        for nbit, site, sbasis in zip(nbits, exp_struct, site_bases):
            # Coordinates of point terms must stay in [0, 1] to guarantee
            # correct math of the following algorithm.
            new_orbit = Orbit(
                [np.mod(site.frac_coords, 1)], exp_struct.lattice,
                [list(range(nbit))], [sbasis], symops)
            if remove_last:
                new_orbit.remove_bit_combos_by_inds(
                    [len(new_orbit.bit_combos) - 1])
            if new_orbit not in new_orbits:
                new_orbits.append(new_orbit)

        orbits[1] = sorted(
            new_orbits,
            key=lambda x: (np.round(x.base_cluster.diameter, 6),
                           -x.multiplicity))

        if len(cutoffs) == 0:  # return singlets only if no cutoffs provided
            return orbits

        max_lp = np.linalg.norm(exp_struct.lattice.matrix.sum(axis=0)) / 2
        max_lp += SITE_TOL
        for size, diameter in sorted(cutoffs.items()):
            new_orbits = []
            neighbors = exp_struct.get_sites_in_sphere(
                [0.5, 0.5, 0.5], diameter + max_lp, include_index=True)
            for orbit in orbits[size-1]:
                if orbit.base_cluster.diameter > diameter:
                    continue
                for neighbor in neighbors:
                    if is_coord_subset(
                            [neighbor.frac_coords], orbit.base_cluster.sites,
                            atol=SITE_TOL):
                        continue
                    new_sites = np.concatenate(
                        [orbit.base_cluster.sites, [neighbor.frac_coords]])
                    new_orbit = Orbit(
                        new_sites, exp_struct.lattice,
                        orbit.bits + [list(range(nbits[neighbor.index]))],
                        orbit.site_bases + [site_bases[neighbor.index]],
                        symops)

                    if remove_last:
                        new_orbit.remove_bit_combos_by_inds(
                            [len(new_orbit.bit_combos) - 1])
                    if new_orbit.base_cluster.diameter > diameter + 1e-8:
                        continue
                    elif new_orbit not in new_orbits:
                        new_orbits.append(new_orbit)

            if len(new_orbits) > 0:
                orbits[size] = sorted(
                    new_orbits,
                    key=lambda x: (np.round(x.base_cluster.diameter, 6),
                                   -x.multiplicity))
        return orbits

    def get_function_decoration(self, index):
        """Get the decoration/labeling of a specific orbit function.

        When using an indicator site basis there is 1 to 1 equivalence between
        correlation functions and species decorations.

        Args:
            index (int):
                index of orbit function in correlation vector

        Returns:
            list of tuples: list of tuples of symmetrically equivalent
            Species/Elements.
        """
        orbit = self.orbits[self.function_orbit_ids[index] - 1]
        decorations = [
            tuple(list(orbit.site_spaces[i])[b] for i, b in enumerate(bits))
            for bits in orbit.bit_combos[index - orbit.bit_id]
        ]
        return decorations

    def get_orbit_decorations(self, orbit_id):
        """Get all decorations/labellings of species in an orbit.

        Args:
            orbit_id (int):
                if of orbit

        Returns:
            list of list: list of lists of symmetrically equivalent
            Species/Elements.
        """
        bit_id = self.orbits[orbit_id - 1].bit_id
        num_combos = len(self.orbits[orbit_id - 1].bit_combos)
        return [self.get_function_decoration(bid)
                for bid in range(bit_id, bit_id + num_combos)]

    @staticmethod
    def _gen_orbits_from_cutoffs(exp_struct, cutoffs, symops, remove_last):
        """Generate orbits from diameter cutoffs.

        Generates dictionary of orbits in the same way that the cluster
        subspace class does, except that the orbit functions (and corresponding
        bit combos) include all symmetrically distinct decorations/labelings
        of indicator functions for all allowed species (except 1 decoration
        for each orbit since this value is just 1 - sum of concentration of all
        other decorations

        Args:
            exp_struct (Structure):
                Structure with all sites that have partial occupancy.
            cutoffs (dict):
                dict of cutoffs for cluster diameters {size: cutoff}
            symops (list of SymmOps):
                list of symmetry operations for structure
            remove_last (bool):
                remove the last cluster labeling from each orbit.

        Returns:
            dict: {size: list of Orbits within diameter cutoff}
        """

        site_spaces = get_site_spaces(exp_struct)
        site_bases = tuple(
            IndicatorBasis(site_space) for site_space in site_spaces)
        orbits = {}
        nbits = np.array([len(b) for b in site_spaces])

        try:
            if cutoffs.pop(1) is None:
                if len(cutoffs) != 0:
                    raise ValueError(
                        f"Unable to generate clusters of higher order "
                        f" {cutoffs} if point terms are excluded."
                    )
                return {}
        except KeyError:
            pass

        # Generate singlet/point orbits
        orbits[1] = ClusterSubspace._gen_point_orbits(exp_struct,
                                                      site_bases,
                                                      nbits, symops)

        if len(cutoffs) == 0:  # return singlets only if no cutoffs provided
            return orbits

        orbits.update(
            ClusterSubspace._gen_multi_orbits(orbits[1], exp_struct, cutoffs,
                                              site_bases, nbits, symops)
        )

        if remove_last:
            for orbs in orbits.values():
                for orb in orbs:
                    orb.remove_bit_combos_by_inds([len(orb.bit_combos) - 1])

        return orbits


    def as_dict(self):
        """
        Json-serialization dict representation.

        Returns:
            MSONable dict
        """
        d = super().as_dict()
        d['_wo_last_cluster'] = self._wo_last_cluster
        return d

    @classmethod
    def from_dict(cls, d):
        """Create ClusterSubspace from an MSONable dict."""
        subspace = super().from_dict(d)
        subspace._wo_last_cluster = d.get('_wo_last_cluster', True)
        # remove last bit combo in all orbits
        if subspace._wo_last_cluster:
            for orbit in subspace.orbits:
                orbit.remove_bit_combos_by_inds([len(orbit.bit_combos) - 1])
        subspace._assign_orbit_ids()
        return subspace
