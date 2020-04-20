import unittest
import random
import numpy as np
from itertools import combinations
from pymatgen import Lattice, Structure
from pymatgen.util.coord import is_coord_subset_pbc
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from smol.cofe import ClusterSubspace
from smol.cofe.configspace.utils import SITE_TOL, get_bits
from smol.exceptions import *
from src.ce_utils import corr_from_occupancy


class TestClusterSubSpace(unittest.TestCase):
    def setUp(self) -> None:
        self.lattice = Lattice([[3, 3, 0], [0, 3, 3], [3, 0, 3]])
        self.species = [{'Li': 0.1, 'Ca': 0.1}] * 3 + ['Br']
        self.coords = ((0.25, 0.25, 0.25), (0.75, 0.75, 0.75),
                       (0.5, 0.5, 0.5), (0, 0, 0))
        self.structure = Structure(self.lattice, self.species, self.coords)
        sf = SpacegroupAnalyzer(self.structure)
        self.symops = sf.get_symmetry_operations()
        self.cs = ClusterSubspace.from_radii(self.structure, {2: 6, 3: 5},
                                             basis='Indicator',
                                             orthonormal=False)
        self.bits = get_bits(self.structure)

    def test_numbers(self):
        # Test the total generated orbits, orderings and clusters are as expected.
        self.assertEqual(self.cs.n_orbits, 27)
        self.assertEqual(self.cs.n_bit_orderings, 124)
        self.assertEqual(self.cs.n_clusters, 377)

    def test_orbits(self):
        self.assertEqual(len(self.cs.orbits) + 1, self.cs.n_orbits)  # +1 for empty cluster
        for o1, o2 in combinations(self.cs.orbits, 2):
            self.assertNotEqual(o1, o2)

    def test_iterorbits(self):
        orbits = [o for o in self.cs.iterorbits()]
        for o1, o2 in zip(orbits, self.cs.orbits):
            self.assertEqual(o1, o2)

    def test_bases_ortho(self):
        # test orthogonality, orthonormality of bases with uniform and
        # concentration measure
        self.assertFalse(self.cs.basis_orthogonal)
        self.assertFalse(self.cs.basis_orthonormal)
        cs = ClusterSubspace.from_radii(self.structure, {2: 6, 3: 5},
                                        basis='Indicator', orthonormal=True)
        self.assertTrue(cs.basis_orthogonal)
        self.assertTrue(cs.basis_orthonormal)
        cs = ClusterSubspace.from_radii(self.structure, {2: 6, 3: 5},
                                        basis='sinusoid')
        self.assertTrue(cs.basis_orthogonal)
        # Not orthonormal w.r.t. to uniform measure...
        self.assertFalse(cs.basis_orthonormal)
        cs = ClusterSubspace.from_radii(self.structure, {2: 6, 3: 5},
                                        basis='sinusoid', orthonormal=True)
        self.assertTrue(cs.basis_orthonormal)
        cs = ClusterSubspace.from_radii(self.structure, {2: 6, 3: 5},
                                        basis='sinusoid',
                                        use_concentration=True)
        # Not orthogonal/normal wrt to concentration measure
        self.assertFalse(cs.basis_orthogonal)
        self.assertFalse(cs.basis_orthonormal)
        cs = ClusterSubspace.from_radii(self.structure, {2: 6, 3: 5},
                                        basis='sinusoid', orthonormal=True,
                                        use_concentration=True)
        self.assertTrue(cs.basis_orthogonal)
        self.assertTrue(cs.basis_orthonormal)
        cs = ClusterSubspace.from_radii(self.structure, {2: 6, 3: 5},
                                        basis='indicator', orthonormal=True,
                                        use_concentration=True)
        self.assertTrue(cs.basis_orthogonal)
        self.assertTrue(cs.basis_orthonormal)

    #  These can probably be improved to check odd and specific cases we want
    #  to watch out for
    def test_supercell_matrix_from_structure(self):
        # Simple scaling
        supercell = self.structure.copy()
        supercell.make_supercell(2)
        sc_matrix = self.cs.scmatrix_from_structure(supercell)
        self.assertAlmostEqual(np.linalg.det(sc_matrix), 8)

        # A more complex supercell_structure
        m = np.array([[ 0,  5,  3],
                      [-2,  0,  2],
                      [-2,  4,  3]])
        supercell = self.structure.copy()
        supercell.make_supercell(m)
        sc_matrix = self.cs.scmatrix_from_structure(supercell)
        self.assertAlmostEqual(np.linalg.det(sc_matrix), abs(np.linalg.det(m)))

        # Test a slightly distorted structure
        lattice = Lattice([[2.95, 3, 0], [0, 3, 2.9], [3, 0, 3]])
        structure = Structure(lattice, self.species, self.coords)
        supercell = structure.copy()
        supercell.make_supercell(2)
        sc_matrix = self.cs.scmatrix_from_structure(supercell)
        self.assertAlmostEqual(np.linalg.det(sc_matrix), 8)

        m = np.array([[0, 5, 3],
                      [-2, 0, 2],
                      [-2, 4, 3]])
        supercell = structure.copy()
        supercell.make_supercell(m)
        sc_matrix = self.cs.scmatrix_from_structure(supercell)
        self.assertAlmostEqual(np.linalg.det(sc_matrix), abs(np.linalg.det(m)))

    def test_refine_structure(self):
        lattice = Lattice([[2.95, 3, 0], [0, 3, 2.9], [3, 0, 3]])
        structure = Structure(lattice, ['Li', ] * 2 + ['Ca'] + ['Br'], self.coords)
        structure.make_supercell(2)
        ref_structure = self.cs.refine_structure(structure)
        prim_ref = ref_structure.get_primitive_structure()

        # This is failing in pymatgen 2020.1.10, since lattice matrices are not the same,
        # but still equivalent
        # self.assertEqual(self.lattice, structure.lattice)
        self.assertTrue(np.allclose(self.lattice.parameters, prim_ref.lattice.parameters))
        self.assertTrue(np.allclose(self.cs.corr_from_structure(structure),
                                    self.cs.corr_from_structure(ref_structure)))

    def test_corr_from_structure(self):
        structure = Structure(self.lattice, ['Li',] * 2 + ['Ca'] + ['Br'], self.coords)
        corr = self.cs.corr_from_structure(structure)
        self.assertEqual(len(corr), self.cs.n_bit_orderings + len(self.cs.external_terms))
        self.assertEqual(corr[0], 1)

        cs = ClusterSubspace.from_radii(self.structure, {2: 5})

        # make an ordered supercell_structure
        s = self.structure.copy()
        s.make_supercell([2, 1, 1])
        species = ('Li', 'Ca', 'Li', 'Ca', 'Br', 'Br')
        coords = ((0.125, 0.25, 0.25),
                  (0.625, 0.25, 0.25),
                  (0.375, 0.75, 0.75),
                  (0.25, 0.5, 0.5),
                  (0, 0, 0),
                  (0.5, 0, 0))
        s = Structure(s.lattice, species, coords)
        self.assertEqual(len(cs.corr_from_structure(s)), 22)
        expected = [1, 0.5, 0.25, 0, 0.5, 0, 0.375, 0, 0.0625, 0.25, 0.125,
                    0, 0.25, 0.125, 0.125, 0, 0, 0.25, 0, 0.125, 0, 0.1875]
        self.assertTrue(np.allclose(cs.corr_from_structure(s), expected))

        # Test occu_from_structure
        occu = ['Vacancy', 'Li', 'Ca', 'Li', 'Vacancy', 'Ca', 'Br', 'Br']
        self.assertTrue(all(s1 == s2 for s1, s2
                            in zip(occu, cs.occupancy_from_structure(s))))

        # shuffle sites and check correlation still works
        for _ in range(10):
            random.shuffle(s)
            self.assertTrue(np.allclose(cs.corr_from_structure(s), expected))

    def test_remove_orbits(self):
        cs = ClusterSubspace.from_radii(self.structure, {2: 5})
        s = self.structure.copy()
        s.make_supercell([2, 1, 1])
        species = ('Li', 'Ca', 'Li', 'Ca', 'Br', 'Br')
        coords = ((0.125, 0.25, 0.25),
                  (0.625, 0.25, 0.25),
                  (0.375, 0.75, 0.75),
                  (0.25, 0.5, 0.5),
                  (0, 0, 0),
                  (0.5, 0, 0))
        s = Structure(s.lattice, species, coords)
        self.assertRaises(ValueError, cs.remove_orbits, [-1])
        self.assertRaises(ValueError, cs.remove_orbits,
                          [cs.n_orbits + 1])
        self.assertRaises(ValueError, cs.remove_orbits, [0])
        cs.remove_orbits([3, 5, 7])
        expected = [1, 0.5, 0.25, 0, 0.5, 0.25, 0.125, 0, 0, 0, 0.25]
        self.assertEqual(len(cs.corr_from_structure(s)), 11)
        self.assertEqual(cs.n_orbits, 5)
        self.assertTrue(np.allclose(cs.corr_from_structure(s), expected))

    def test_remove_bit_combos(self):
        cs = ClusterSubspace.from_radii(self.structure, {2: 5})
        s = self.structure.copy()
        s.make_supercell([2, 1, 1])
        species = ('Li', 'Ca', 'Li', 'Ca', 'Br', 'Br')
        coords = ((0.125, 0.25, 0.25),
                  (0.625, 0.25, 0.25),
                  (0.375, 0.75, 0.75),
                  (0.25, 0.5, 0.5),
                  (0, 0, 0),
                  (0.5, 0, 0))
        s = Structure(s.lattice, species, coords)
        remove = [9, 10, 18] #{4: [[0, 0], [0, 1]], 7: [[0, 0]]}
        new_n_orderings = cs.n_bit_orderings - len(remove)

        cs.remove_orbit_bit_combos(remove)
        self.assertEqual(cs.n_bit_orderings, new_n_orderings)
        expected = [1, 0.5, 0.25, 0, 0.5, 0, 0.375, 0, 0.0625,
                    0, 0.25, 0.125, 0.125, 0, 0, 0.25, 0.125, 0, 0.1875]
        self.assertTrue(np.allclose(cs.corr_from_structure(s), expected))
        self.assertWarns(UserWarning, cs.remove_orbit_bit_combos, [9])

    def test_orbit_mappings_from_matrix(self):
        # check that all supercell_structure index groups map to the correct
        # primitive cell sites, and check that max distance under supercell
        # structure pbc is less than the max distance without pbc

        m = np.array([[2, 0, 0], [0, 2, 0], [0, 1, 1]])
        supercell_struct = self.structure.copy()
        supercell_struct.make_supercell(m)
        fcoords = np.array(supercell_struct.frac_coords)

        for orb, inds in self.cs.supercell_orbit_mappings(m):
            for x in inds:
                pbc_radius = np.max(supercell_struct.lattice.get_all_distances(
                    fcoords[x], fcoords[x]))
                # primitive cell fractional coordinates
                new_fc = np.dot(fcoords[x], m)
                self.assertGreater(orb.radius + 1e-7, pbc_radius)
                found = False
                for equiv in orb.clusters:
                    if is_coord_subset_pbc(equiv.sites, new_fc, atol=SITE_TOL):
                        found = True
                        break
                self.assertTrue(found)

        # check that the matrix was cached
        m_hash = tuple(sorted(tuple(s) for s in m))
        self.assertTrue(self.cs._supercell_orb_inds[m_hash] is
                        self.cs.supercell_orbit_mappings(m))

    def test_periodicity(self):
        # Check to see if a supercell of a smaller structure gives the same corr
        m = np.array([[2, 0, 0], [0, 2, 0], [0, 1, 1]])
        supercell = self.structure.copy()
        supercell.make_supercell(m)
        s = Structure(supercell.lattice, ['Ca', 'Li', 'Li', 'Br', 'Br', 'Br', 'Br'],
                      [[0.125, 1, 0.25], [0.125, 0.5, 0.25], [0.375, 0.5, 0.75],
                       [0, 0, 0], [0, 0.5, 1], [0.5, 1, 0], [0.5, 0.5, 0]])
        cs = ClusterSubspace.from_radii(self.structure, {2: 6, 3: 5})
        a = cs.corr_from_structure(s)
        s.make_supercell([2, 1, 1])
        b = cs.corr_from_structure(s)
        self.assertTrue(np.allclose(a, b))

    def _encode_occu(self, occu, bits):
        return np.array([bit.index(sp) for sp, bit in zip(occu, bits)])

    def test_vs_CASM_pairs(self):
        species = [{'Li':0.1}] * 3 + ['Br']
        coords = ((0.25, 0.25, 0.25), (0.75, 0.75, 0.75),
                  (0.5, 0.5, 0.5),  (0, 0, 0))
        structure = Structure(self.lattice, species, coords)
        cs = ClusterSubspace.from_radii(structure, {2: 6})
        bits = get_bits(structure)
        m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        orbit_list = [(orb.bit_id, orb.bit_combos, orb.bases_array, inds)
                      for orb, inds in cs.supercell_orbit_mappings(m)]

        # last two clusters are switched from CASM output (and using occupancy basis)
        # all_li (ignore casm point term)
        occu = self._encode_occu(['Li', 'Li', 'Li'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr, np.array([1]*12)))

        # all_vacancy
        occu = self._encode_occu(['Vacancy', 'Vacancy', 'Vacancy'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    np.array([1]+[0]*11)))
        # octahedral
        occu = self._encode_occu(['Vacancy', 'Vacancy', 'Li'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1]))
        # tetrahedral
        occu = self._encode_occu(['Li', 'Li', 'Vacancy'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0]))
        # mixed
        occu = self._encode_occu(['Li', 'Vacancy', 'Li'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 0.5, 1, 0.5, 0, 0.5, 1, 0.5, 0, 0, 0.5, 1]))
        # single_tet
        occu = self._encode_occu(['Li', 'Vacancy', 'Vacancy'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 0.5, 0, 0, 0, 0.5, 0, 0, 0, 0, 0.5, 0]))

    def test_vs_CASM_triplets(self):
        """
        test vs casm generated correlation with occupancy basis
        """
        species = [{'Li': 0.1}] * 3 + ['Br']
        coords = ((0.25, 0.25, 0.25), (0.75, 0.75, 0.75),
                  (0.5, 0.5, 0.5), (0, 0, 0))
        structure = Structure(self.lattice, species, coords)
        cs = ClusterSubspace.from_radii(structure, {2: 6, 3: 4.5})
        bits = get_bits(structure)
        m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        orbit_list = [(orb.bit_id, orb.bit_combos, orb.bases_array, inds)
                      for orb, inds in cs.supercell_orbit_mappings(m)]
        # last two pair terms are switched from CASM output (and using occupancy basis)
        # all_vacancy (ignore casm point term)
        occu = self._encode_occu(['Vacancy', 'Vacancy', 'Vacancy'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr, np.array([1] + [0] * 18)))
        # all_li
        occu = self._encode_occu(['Li', 'Li', 'Li'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr, np.array([1] * 19)))
        # octahedral
        occu = self._encode_occu(['Vacancy', 'Vacancy', 'Li'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1]))

        # tetrahedral
        occu = self._encode_occu(['Li', 'Li', 'Vacancy'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 0, 0, 1, 1, 0]))
        # mixed
        occu = self._encode_occu(['Li', 'Vacancy', 'Li'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 0.5, 1, 0.5, 0, 0.5, 1, 0.5, 0, 0, 0.5, 1, 0, 0, 0.5, 0.5, 0.5, 0.5, 1]))
        # single_tet
        occu = self._encode_occu(['Li', 'Vacancy', 'Vacancy'], bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 0.5, 0, 0, 0, 0.5, 0, 0, 0, 0, 0.5, 0, 0, 0, 0, 0, 0.5, 0.5, 0]))

    def test_vs_CASM_multicomp(self):
        cs = ClusterSubspace.from_radii(self.structure, {2: 5})
        m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

        orbit_list = [(orb.bit_id, orb.bit_combos, orb.bases_array, inds)
                      for orb, inds in cs.supercell_orbit_mappings(m)]
        # mixed
        occu = self._encode_occu(['Vacancy', 'Li', 'Li'], self.bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 0.5, 0, 1, 0, 0.5, 0, 0, 0, 0, 0, 0, 0.5, 0, 0, 1, 0, 0, 0.5, 0, 0, 0]))
        # Li_tet_ca_oct
        occu = self._encode_occu(['Vacancy', 'Li', 'Ca'], self.bits)
        corr = corr_from_occupancy(occu, cs.n_bit_orderings, orbit_list)
        self.assertTrue(np.allclose(corr,
                                    [1, 0.5, 0, 0, 1, 0, 0.5, 0, 0, 0, 0, 0, 0.5, 0, 0, 0, 0, 1, 0, 0.5, 0, 0]))

    def test_copy(self):
        cs = self.cs.copy()
        self.assertFalse(cs is self.cs)
        self.assertTrue(isinstance(cs, ClusterSubspace))

    def test_change_basis(self):
        # make an ordered supercell_structure
        s = self.structure.copy()
        s.make_supercell([2, 1, 1])
        species = ('Li', 'Ca', 'Li', 'Ca', 'Br', 'Br')
        coords = ((0.125, 0.25, 0.25),
                  (0.625, 0.25, 0.25),
                  (0.375, 0.75, 0.75),
                  (0.25, 0.5, 0.5),
                  (0, 0, 0),
                  (0.5, 0, 0))
        s = Structure(s.lattice, species, coords)

        cs = self.cs.copy()
        for basis in ('sinusoid', 'chebyshev', 'legendre'):
            cs.change_site_bases(basis)
            self.assertFalse(np.allclose(cs.corr_from_structure(s),
                                         self.cs.corr_from_structure(s)))
        cs.change_site_bases('indicator', orthonormal=True)
        self.assertFalse(np.allclose(cs.corr_from_structure(s),
                                     self.cs.corr_from_structure(s)))
        self.assertTrue(cs.basis_orthogonal)
        self.assertTrue(cs.basis_orthonormal)
        cs.change_site_bases('indicator', orthonormal=False)
        self.assertTrue(np.allclose(cs.corr_from_structure(s),
                                    self.cs.corr_from_structure(s)))
        self.assertFalse(cs.basis_orthogonal)
        self.assertFalse(cs.basis_orthonormal)

    def test_exceptions(self):
        self.assertRaises(NotImplementedError, ClusterSubspace.from_radii,
                          self.structure, {2: 5}, basis='blobs')
        cs = ClusterSubspace.from_radii(self.structure, {2: 5})
        s = self.structure.copy()
        s.make_supercell([2, 1, 1])
        species = ('X', 'Ca', 'Li', 'Ca', 'Br', 'Br')
        coords = ((0.125, 0.25, 0.25),
                  (0.625, 0.25, 0.25),
                  (0.375, 0.75, 0.75),
                  (0.25, 0.5, 0.5),
                  (0, 0, 0),
                  (0.5, 0, 0))
        s = Structure(s.lattice, species, coords)
        self.assertRaises(StructureMatchError, cs.corr_from_structure, s)

    def test_repr(self):
        repr(self.cs)

    def test_str(self):
        str(self.cs)

    def test_msonable(self):
        d = self.cs.as_dict()
        cs = ClusterSubspace.from_dict(d)
        self.assertEqual(cs.n_orbits, 27)
        self.assertEqual(cs.n_bit_orderings, 124)
        self.assertEqual(cs.n_clusters, 377)
        self.assertEqual(str(cs), str(self.cs))