import unittest
import numpy as np
from numpy.polynomial.chebyshev import chebval
from numpy.polynomial.legendre import legval
from smol.cofe.configspace import basis


available_bases = {'indicator': basis.IndicatorBasis,
                   'sinusoid': basis.SinusoidBasis,
                   'chebyshev': basis.ChebyshevBasis,
                   'legendre': basis.LegendreBasis}


class TestBasis(unittest.TestCase):
    def setUp(self) -> None:
        self.species = {'Li+': 0.5, 'Mn2+': 0.2, 'Vacancy': 0.3}

    def _test_basis_uniform_measure(self, basis_cls):
        b = basis_cls(self.species.keys())
        measure = [b.measure(specie) for specie in self.species.keys()]
        self.assertEqual(measure, len(self.species)*[1/len(self.species), ])

    def _test_measure(self, basis_cls):
        b = basis_cls(self.species)
        measure = [b.measure(specie) for specie in self.species.keys()]
        self.assertEqual(measure, list(self.species.values()))

    def test_indicator_basis(self):
        b = basis.IndicatorBasis(self.species.keys())
        self.assertFalse(b.is_orthogonal)

        #eval(self, fun_ind, specie):
        # test evaluation of basis functions
        n = len(self.species) - 1
        for i in range(n):
            self.assertEqual(b.function_array[i, i], 1)

        self._test_basis_uniform_measure(basis.IndicatorBasis)
        self._test_measure(basis.IndicatorBasis)

        b.orthonormalize()
        self.assertTrue(b.is_orthonormal)

    def test_sinusoid_basis(self):
        b = basis.SinusoidBasis(self.species.keys())
        self.assertTrue(b.is_orthogonal)

        # test evaluation of basis functions
        m = len(self.species)
        for n in range(1, len(self.species)):
            a = -(-n//2)
            f = lambda s: -np.sin(2*np.pi*a*s/m) if n % 2 == 0 else -np.cos(2*np.pi*a*s/m)
            for i, sp in enumerate(self.species):
                self.assertEqual(b.function_array[n-1, i], f(i))

        self._test_basis_uniform_measure(basis.SinusoidBasis)
        self._test_measure(basis.SinusoidBasis)
        b = basis.SinusoidBasis(self.species)
        self.assertFalse(b.is_orthogonal)
        b.orthonormalize()
        self.assertTrue(b.is_orthonormal)

    def test_chebyshev_basis(self):
        species = list(self.species.keys())[:2]
        b = basis.ChebyshevBasis(species)
        self.assertTrue(b.is_orthogonal)  # orthogonal only for 2 species
        b = basis.ChebyshevBasis(self.species.keys())
        self.assertFalse(b.is_orthogonal)

        # test evaluation of basis functions
        m, coeffs = len(self.species), [1]
        fun_range = np.linspace(-1, 1, m)
        for n in range(m-1):
            coeffs.append(0)
            for sp, x in enumerate(fun_range):
                self.assertEqual(b.function_array[n, sp], chebval(x, c=coeffs[::-1]))

        self._test_basis_uniform_measure(basis.ChebyshevBasis)
        self._test_measure(basis.ChebyshevBasis)
        b = basis.ChebyshevBasis(self.species)
        self.assertFalse(b.is_orthogonal)
        b.orthonormalize()
        self.assertTrue(b.is_orthonormal)

    def test_legendre_basis(self):
        species = list(self.species.keys())[:2]
        b = basis.LegendreBasis(species)
        self.assertTrue(b.is_orthogonal)  # orthogonal only for 2 species
        b = basis.LegendreBasis(self.species.keys())
        self.assertFalse(b.is_orthogonal)

        # test evaluation of basis functions
        m, coeffs = len(self.species), [1]
        fun_range = np.linspace(-1, 1, m)
        for n in range(m-1):
            coeffs.append(0)
            for sp, x in enumerate(fun_range):
                self.assertEqual(b.function_array[n, sp],
                                 legval(x, c=coeffs[::-1]))

        self._test_basis_uniform_measure(basis.LegendreBasis)
        self._test_measure(basis.LegendreBasis)
        b = basis.LegendreBasis(self.species)
        self.assertFalse(b.is_orthogonal)
        b.orthonormalize()
        self.assertTrue(b.is_orthonormal)

    def test_warning(self):
        species = {'A': 1, 'B': 2, 'C': 1}
        self.assertWarns(RuntimeWarning, basis.IndicatorBasis, species)
        species = {'A': .1, 'B': .1, 'C': .1}
        self.assertWarns(RuntimeWarning, basis.IndicatorBasis, species)

    def test_basis_factory(self):
        for name in available_bases:
            b = basis.basis_factory(name, self.species)
            self.assertIsInstance(b, available_bases[name])