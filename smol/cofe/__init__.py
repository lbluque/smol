"""
The cofe (pronounced coffee) package contains all the necessary classes and
functions to define and fit cluster expansions for crystalline materials.
"""

from __future__ import division

from .configspace.clusterspace import ClusterSubspace
from .wrangler import StructureWrangler
from .expansion import ClusterExpansion
from .processor import ClusterExpansionProcessor


__all__ = ['ClusterSubspace', 'StructureWrangler', 'ClusterExpansion',
           'ClusterExpansionProcessor']
