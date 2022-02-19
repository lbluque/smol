import os
import pytest
import numpy as np
from monty.serialization import loadfn
from smol.cofe import ClusterSubspace, ClusterExpansion
from smol.moca.processor import ClusterExpansionProcessor, \
    EwaldProcessor, OrbitDecompositionProcessor, CompositeProcessor
from smol.moca import CanonicalEnsemble, SemiGrandEnsemble
from smol.cofe.extern import EwaldTerm

# load test data files and set them up as fixtures
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# some test structures to use in tests
files = ['AuPd_prim.json', 'CrFeW_prim.json', 'LiCaBr_prim.json',
         'LiMOF_prim.json', 'LiMnTiVOF_prim.json']
test_structures = [loadfn(os.path.join(DATA_DIR, file)) for file in files]
ensembles = [CanonicalEnsemble, SemiGrandEnsemble]


@pytest.fixture(params=test_structures, scope='package')
def structure(request):
    return request.param


@pytest.fixture(params=test_structures, scope='module')
def cluster_subspace(request):
    subspace = ClusterSubspace.from_cutoffs(
        request.param, cutoffs={2: 6, 3: 5, 4: 4}, supercell_size='volume')
    return subspace


@pytest.fixture(params=test_structures, scope='module')
def cluster_subspace_ewald(request):
    subspace = ClusterSubspace.from_cutoffs(
        request.param, cutoffs={2: 6, 3: 5, 4: 4}, supercell_size='volume')
    subspace.add_external_term(EwaldTerm())
    return subspace


@pytest.fixture(scope='module')
def single_subspace():
    subspace = ClusterSubspace.from_cutoffs(
        test_structures[2], cutoffs={2: 6, 3: 5, 4: 4}, supercell_size='volume')
    return subspace


@pytest.fixture(scope='module')
def ce_processor(cluster_subspace):
    coefs = 2 * np.random.random(cluster_subspace.num_corr_functions)
    scmatrix = 3 * np.eye(3)
    return ClusterExpansionProcessor(
        cluster_subspace, supercell_matrix=scmatrix, coefficients=coefs)


@pytest.fixture(params=['CE', 'OD'], scope='module')
def composite_processor(cluster_subspace_ewald, request):
    coefs = 2 * np.random.random(cluster_subspace.num_corr_functions + 1)
    scmatrix = 3 * np.eye(3)
    proc = CompositeProcessor(cluster_subspace, supercell_matrix=scmatrix)
    if request.param == 'CE':
        proc.add_processor(
            ClusterExpansionProcessor(cluster_subspace, scmatrix, coefficients=coefs[:-1])
        )
    else:
        expansion = ClusterExpansion(cluster_subspace, coefs)
        proc.add_processor(
            OrbitDecompositionProcessor(cluster_subspace, scmatrix,
                                        expansion.orbit_factor_tensors)
        )
    proc.add_processor(EwaldProcessor(cluster_subspace, scmatrix, EwaldTerm(),
                                      coefficient=coefs[-1]))
    # bind raw coefficients since OD processors do not store them
    # and be able to test computing properties, hacky but oh well
    proc.raw_coefs = coefs
    return proc


@pytest.fixture(params=ensembles, scope='module')
def ensemble(composite_processor, request):
    if request.param is SemiGrandEnsemble:
        kwargs = {'chemical_potentials':
                  {sp: 0.3 for space in composite_processor.unique_site_spaces
                   for sp in space.keys()}}
    else:
        kwargs = {}
    return request.param(composite_processor, **kwargs)


@pytest.fixture(scope='module')
def single_canonical_ensemble(single_subspace):
    coefs = np.random.random(single_subspace.num_corr_functions)
    proc = ClusterExpansionProcessor(single_subspace, 4 * np.eye(3), coefs)
    return CanonicalEnsemble(proc)
