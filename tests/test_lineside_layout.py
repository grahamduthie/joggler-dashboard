"""Regression guards for the /lineside geometry (data/lineside-layout-v2.json)."""

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class LinesideLayoutTests(unittest.TestCase):
    def test_v2_moves_confirmed_reading_platform_14_out_of_running_line(self):
        v1 = json.loads((ROOT / 'data' / 'lineside-layout-v1.json').read_text())
        v2 = json.loads((ROOT / 'data' / 'lineside-layout-v2.json').read_text())
        self.assertIn('1696', v1['reading']['running_line_west']['ur'])
        self.assertEqual(v2['reading']['approach_anchor_berths'], {
            'up_relief': '1676', 'up_main': '1672',
            'down_relief': '1687', 'down_main': '1675',
        })
        self.assertTrue({'1694', '1698', '1696', '1690', '1688',
                         '1695', '1693', '1697', '1685'}
                        .issubset(v2['reading']['remove_from_running_line']))
        self.assertIn('1696', v2['reading']['station_berths_add'])
        self.assertEqual(v2['reading']['berths']['1696']['location'], 'Reading platform 14')
        self.assertEqual(v2['reading']['berths']['1696']['confidence'], 'confirmed')

    def test_v2_is_the_live_layout(self):
        v2 = json.loads((ROOT / 'data' / 'lineside-layout-v2.json').read_text())
        self.assertEqual(v2['status'], 'live')
        source = (ROOT / 'lineside-layout-v2.js').read_text()
        self.assertIn("id: 'v2'", source)
        self.assertIn('removeFromRunningLine', source)
        self.assertIn("'1642':480, '1646':480", source)
        self.assertIn("'1622':770, '1618':770, '1631':760, '1625':770", source)
        self.assertIn("crossovers: 'track_gaps_diagonal_only'", source)
        self.assertIn("'← DOWN RELIEF'", (ROOT / 'lineside.html').read_text())
        self.assertIn("boxes on one running line never overlap", (ROOT / 'lineside.html').read_text())

    def test_v2_records_column_alignment_as_schematic_not_surveyed(self):
        v2 = json.loads((ROOT / 'data' / 'lineside-layout-v2.json').read_text())
        self.assertEqual(v2['alignment']['mode'], 'review_columns_not_distance_survey')
        self.assertIn('do not claim surveyed berth spacing', v2['alignment']['limitations'])
        self.assertEqual(v2['geometry_rules']['crossovers'],
                         'Connect running-track gaps, never berth cells; use diagonal leads only.')
        self.assertIn('adjacent-track crossover ladders',
                      v2['geometry_rules']['multi_track_moves'])

    def test_kennet_siding_is_a_separate_v2_route(self):
        v2 = json.loads((ROOT / 'data' / 'lineside-layout-v2.json').read_text())
        siding = v2['sidings'][0]
        self.assertEqual(siding['id'], 'kennet-loop-siding')
        self.assertEqual(siding['berths'], ['1679'])
        self.assertEqual((siding['from_berth'], siding['to_berth']), ('1669', '1687'))
        self.assertEqual(siding['line'], 'dr')
        self.assertIn('1679', v2['running_line_exclusions'])

    def test_osm_signal_and_siding_anchors_are_reviewable(self):
        osm = json.loads((ROOT / 'data' / 'lineside-osm-anchors.json').read_text())
        self.assertEqual(osm['source']['licence'], 'ODbL 1.0')
        self.assertEqual([a['berth'] for a in osm['signal_anchors']],
                         ['1650', '1646', '1640', '1626', '1602'])
        self.assertEqual([(a['td_signal']['address'], a['td_signal']['bit'])
                          for a in osm['signal_anchors']],
                         [('4A', 2), ('4A', 1), ('4A', 0), ('49', 5), ('16', 0)])
        kennet = next(a for a in osm['track_anchors'] if a['name'] == 'Kennet Loop')
        self.assertEqual(kennet['service'], 'siding')
        self.assertEqual(kennet['td_berths'], ['1679'])
        self.assertEqual(kennet['osm_ways'], [167584119, 198517358, 198517359])
        self.assertEqual(kennet['maxspeed'], '25 mph')
        self.assertIn('approximately 0.7 km', kennet['review']['geometry_finding'])

    def test_named_signal_audit_rejects_the_fixed_grid_as_geographic_scale(self):
        osm = json.loads((ROOT / 'data' / 'lineside-osm-anchors.json').read_text())
        audit = osm['signal_position_audit']
        self.assertIn('do not preserve', audit['finding'])
        self.assertEqual(
            [row['current_svg_gap_px_approx'] for row in audit['up_main_named_signal_spacing']],
            [128, 25, 46, 310])


if __name__ == '__main__':
    unittest.main()
