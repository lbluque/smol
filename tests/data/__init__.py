import os
import json
import numpy as np
from pymatgen import Structure

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')

# Synthetic ClusterExpansion FCC binary data
file_name = 'synthetic-CE-fccbinary-sinebasis.json'
with open(os.path.join(DATA_DIR, file_name), 'r') as f:
    synthetic_CE_binary = json.load(f)
    synthetic_CE_binary['data'] = [(Structure.from_dict(s[0]), s[1])
                                   for s in synthetic_CE_binary['data']]

# Synthetic ClusterExpansion with Ewald electrostatics FCC binary data
file_name = 'synthetic-CE-ewald-neutral-fccbinary-sinebasis.json'
with open(os.path.join(DATA_DIR, file_name), 'r') as f:
    synthetic_CEewald_binary = json.load(f)
    # Load ewald only energy
    synthetic_CEewald_binary['ewald_data'] = [(Structure.from_dict(s[0]), s[1])
                                              for s in
                                              synthetic_CEewald_binary['ewald_data']]
    # Load synthetic CE with ewald energy
    synthetic_CEewald_binary['data'] = [(Structure.from_dict(s[0]), s[1])
                                        for s in
                                        synthetic_CEewald_binary['data']]

# LNO example data
with open(os.path.join(DATA_DIR, 'lno_prim.json'), 'r') as f:
    lno_prim = Structure.from_dict(json.load(f))

with open(os.path.join(DATA_DIR, 'lno_fitting_data.json'), 'r') as f:
    lno_data = [(Structure.from_dict(x['s']), x['toten']) for x in json.load(f)]

# icet AuPt benchmark data
with open(os.path.join(DATA_DIR, 'icet_aupd', 'aupd_prim.json'), 'r') as f:
    aupt_prim = Structure.from_dict(json.load(f))

with open(os.path.join(DATA_DIR, 'icet_aupd', 'aupd_linreg_eci.json'), 'r') as f:
    icet_eci = np.array(json.load(f))

with open(os.path.join(DATA_DIR, 'icet_aupd', 'aupd_predictions.json'), 'r') as f:
    icet_predictions = np.array(json.load(f))

with open(os.path.join(DATA_DIR, 'icet_aupd', 'fit_structs.json'), 'r') as f:
    icet_fit_structs = json.load(f)
    for item in icet_fit_structs:
        item['structure'] = Structure.from_dict(item['structure'])
        item['scmatrix'] = np.array(item['scmatrix'])

with open(os.path.join(DATA_DIR, 'icet_aupd', 'test_structs.json'), 'r') as f:
    icet_test_structs = json.load(f)
    for item in icet_test_structs:
        item['structure'] = Structure.from_dict(item['structure'])
        item['scmatrix'] = np.array(item['scmatrix'])
