"""Regression tests for the /trains accuracy model.

Run with:
    python3 -m unittest tests/test_train_accuracy.py
"""

import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('transport_proxy', ROOT / 'transport-proxy.py')
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


class TrainAccuracyTests(unittest.TestCase):
    def test_rtt_forecast_is_not_stored_as_actual(self):
        service = {
            'scheduleMetadata': {
                'uniqueIdentity': 'U1', 'trainReportingIdentity': '2A00',
                'operator': {'code': 'XR', 'name': 'Elizabeth Line'},
                'inPassengerService': True,
            },
            'temporalData': {
                'displayAs': 'CALL',
                'arrival': {'scheduleAdvertised': '2026-08-16T12:00:00',
                            'realtimeForecast': '2026-08-16T12:02:00'},
                'departure': {'scheduleAdvertised': '2026-08-16T12:01:00',
                              'realtimeForecast': '2026-08-16T12:03:00'},
            },
            'locationMetadata': {'line': {'planned': 'URL'}},
            'origin': [{'location': {'description': 'Reading'},
                        'temporalData': {'scheduleAdvertised': '2026-08-16T11:30:00'}}],
            'destination': [{'location': {'description': 'London Paddington'},
                             'temporalData': {'scheduleAdvertised': '2026-08-16T13:00:00'}}],
        }
        train = proxy._rtt_normalise(service, confirmed=True)
        self.assertEqual(train['twy_actual'], '')
        self.assertEqual(train['twy_forecast'], '2026-08-16T12:03:00')
        self.assertEqual(train['twy_arr_actual'], '')
        self.assertEqual(train['twy_arr_forecast'], '2026-08-16T12:02:00')

    def test_calibration_never_moves_observed_passage(self):
        train = {'display_pass_ts': 1_000, 'house_pass_ts': 1_000,
                 'observed_pass_ts': 1_000, 'pass_time_source': 'td_crossing'}
        self.assertFalse(proxy._apply_train_calibration(train, 31))
        self.assertEqual(train['display_pass_ts'], 1_000)
        self.assertEqual(train['house_pass_ts'], 1_000)

    def test_calibration_moves_a_live_prediction(self):
        train = {'display_pass_ts': 1_000, 'house_pass_ts': 1_000,
                 'observed_pass_ts': 0, 'berth_eta_pass_ts': 1_000,
                 'pass_time_source': 'td_eta'}
        self.assertTrue(proxy._apply_train_calibration(train, 31))
        self.assertEqual(train['display_pass_ts'], 1_031)
        self.assertEqual(train['berth_eta_pass_ts'], 1_031)

    def test_observed_or_post_house_train_is_passed_not_now(self):
        observed = {'display_pass_ts': 980, 'observed_pass_ts': 980,
                    'pass_time_source': 'td_crossing', 'direction': 'up'}
        proxy._finalise_train_state(observed, 1_000)
        self.assertEqual(observed['movement_state'], 'passed')
        self.assertEqual(observed['pass_confidence'], 'observed')

        post_house = {'display_pass_ts': 1_020, 'observed_pass_ts': 0,
                      'pass_time_source': 'td_eta', 'direction': 'up',
                      'td_dist_mi': -0.4, 'td_berth_age': 10}
        proxy._finalise_train_state(post_house, 1_000)
        self.assertEqual(post_house['movement_state'], 'passed')
        self.assertEqual(post_house['passed_evidence'], 'td_post_house')

    def test_fresh_remote_td_berth_overrides_rtt_station_status(self):
        # Mirrors 2P85: RTT retained AT_PLATFORM, while TD showed D1/1652
        # two miles away and held at a signal.
        self.assertTrue(proxy._td_berth_rejects_station({'dist_mi': 2.0}, 271))
        self.assertFalse(proxy._td_berth_rejects_station({'dist_mi': 0.2}, 20))
        self.assertFalse(proxy._td_berth_rejects_station({'dist_mi': 2.0}, 301))

        old_info = proxy._berth_info
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '1660', 'to': '1652',
                                     'descr': '2P85', 'ts': 729}]
        try:
            proxy._berth_info = lambda area, berth: {
                'line': 'Relief', 'dist_mi': 2.0, 'stanme': 'RUSCOMBE'}
            train = {'headcode': '2P85', 'direction': 'up', 'track': 'Relief',
                     'at_station': True, 'call_type': 'STOP', 'passenger': True,
                     'display_pass_ts': 1_000, 'house_pass_ts': 1_000,
                     'pass_time_source': 'rtt_forecast', 'observed_pass_ts': 0}
            proxy._td_enrich_trains([train], 1_000)
            self.assertFalse(train['at_station'])
            self.assertEqual(train['station_evidence'], 'td_not_at_station')
            self.assertTrue(train['held'])
            self.assertEqual(train['pass_time_source'], 'td_eta')
        finally:
            proxy._berth_info = old_info
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_confirmed_red_signal_qualifies_a_held_train(self):
        key = 'D6|1640|east'
        with proxy._sig_lock:
            old_confirmed = dict(proxy._sig_confirmed)
            proxy._sig_confirmed.clear()
            proxy._sig_confirmed[key] = {'addr': '14', 'bit': 0}
        with proxy._sf_lock:
            old_sf = dict(proxy._sf_state)
            proxy._sf_state.clear()
            proxy._sf_state[('D6', '14')] = {'data': '00', 'ts': 1_000, 'src': 'SF'}
        try:
            train = {'display_pass_ts': 1_100, 'pass_time_source': 'td_eta',
                     'direction': 'up', 'held': True, 'td_area': 'D6',
                     'td_berth': '1640'}
            proxy._finalise_train_state(train, 1_000)
            self.assertEqual(train['movement_state'], 'held_at_red')
            self.assertEqual(train['signal_mapping_tier'], 'confirmed')
        finally:
            with proxy._sig_lock:
                proxy._sig_confirmed.clear()
                proxy._sig_confirmed.update(old_confirmed)
            with proxy._sf_lock:
                proxy._sf_state.clear()
                proxy._sf_state.update(old_sf)

    def test_legacy_projection_preserves_old_forecast_and_timestamp(self):
        v2 = {
            'uid': 'U1', 'headcode': '2A00', 'direction': 'up', 'track': 'Main',
            'legacy_track': 'Relief', 'legacy_house_pass_ts': 1_100,
            'house_pass_ts': 1_000, 'display_pass_ts': 1_000,
            'twy_actual': '', 'legacy_twy_actual': '2026-08-16T12:00:00',
            'movement_state': 'passed', 'pass_confidence': 'observed',
        }
        legacy = proxy._legacy_train_projection(v2)
        self.assertEqual(legacy['track'], 'Relief')
        self.assertEqual(legacy['house_pass_ts'], 1_100)
        self.assertEqual(legacy['twy_actual'], '2026-08-16T12:00:00')
        self.assertNotIn('movement_state', legacy)
        self.assertNotIn('display_pass_ts', legacy)

    def test_source_tier_orders_td_over_rtt_over_schedule(self):
        candidates = [
            {'id': 'schedule', 'ts': 100, 'source': 'schedule'},
            {'id': 'rtt_forecast', 'ts': 200, 'source': 'rtt_forecast'},
            {'id': 'td_eta', 'ts': 300, 'source': 'td_eta'},
        ]
        best = proxy._select_headline_candidate(
            candidates, ts_key=lambda c: c['ts'], source_key=lambda c: c['source'])
        self.assertEqual(best['id'], 'td_eta')

    def test_source_tier_breaks_ties_within_a_tier_by_soonest(self):
        candidates = [
            {'id': 'far', 'ts': 500, 'source': 'td_eta'},
            {'id': 'near', 'ts': 200, 'source': 'td_eta'},
        ]
        best = proxy._select_headline_candidate(
            candidates, ts_key=lambda c: c['ts'], source_key=lambda c: c['source'])
        self.assertEqual(best['id'], 'near')

    def test_without_source_key_soonest_still_wins(self):
        candidates = [{'id': 'a', 'ts': 200}, {'id': 'b', 'ts': 100}]
        best = proxy._select_headline_candidate(candidates, ts_key=lambda c: c['ts'])
        self.assertEqual(best['id'], 'b')

    def test_ranked_model_prefers_live_td_eta_over_an_earlier_schedule_phantom(self):
        # Reproduces the dominant catastrophic failure found in the evidence
        # log backtest: a stale schedule-only entry with an earlier number
        # out-ranks the real, live-confirmed train under soonest-wins.
        trains = [
            {'uid': 'phantom', 'run_key': 'phantom', 'direction': 'up', 'track': 'Main',
             'legacy_track': 'Main', 'legacy_house_pass_ts': 1_050,
             'house_pass_ts': 1_050, 'pass_time_source': 'schedule'},
            {'uid': 'real', 'run_key': 'real', 'direction': 'up', 'track': 'Main',
             'legacy_track': 'Main', 'legacy_house_pass_ts': 1_090,
             'house_pass_ts': 1_090, 'pass_time_source': 'td_eta'},
        ]
        legacy_keys = proxy._headline_run_keys(trains, 1_000, 'legacy')
        ranked_keys = proxy._headline_run_keys(trains, 1_000, 'ranked')
        self.assertEqual(legacy_keys['um'], 'phantom')
        self.assertEqual(ranked_keys['um'], 'real')

    def test_legacy_and_v2_headline_selection_are_independent(self):
        # legacy and v2 use different track classification and eligibility
        # rules internally (see _headline_run_keys); this is still exercised
        # by the background evidence scoring even though no public endpoint
        # lets a client pick a model.
        trains = [
            {'uid': 'old', 'run_key': 'old', 'direction': 'up', 'track': 'Main',
             'legacy_track': 'Relief', 'legacy_house_pass_ts': 1_010,
             'display_pass_ts': 1_100, 'house_pass_ts': 1_100,
             'legacy_twy_actual': '', 'movement_state': 'approaching'},
            {'uid': 'new', 'run_key': 'new', 'direction': 'up', 'track': 'Relief',
             'legacy_track': 'Relief', 'legacy_house_pass_ts': 1_060,
             'display_pass_ts': 1_020, 'house_pass_ts': 1_020,
             'legacy_twy_actual': '', 'movement_state': 'approaching'},
        ]
        legacy_keys = proxy._headline_run_keys(trains, 1_000, 'legacy')
        v2_keys = proxy._headline_run_keys(trains, 1_000, 'v2')
        self.assertEqual(legacy_keys['ur'], 'old')
        self.assertEqual(v2_keys['ur'], 'new')

    def test_td_house_crossing_scores_saved_model_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_file = pathlib.Path(tmp) / 'evidence.jsonl'
            old_file = proxy.TRAIN_EVIDENCE_FILE
            proxy.TRAIN_EVIDENCE_FILE = str(evidence_file)
            with proxy._evidence_lock:
                proxy._evidence_recent.clear()
                proxy._evidence_scored_crossings.clear()
                proxy._evidence_last_signature = ''
                proxy._evidence_last_record_ts = 0
            try:
                train = {
                    'uid': 'U1', 'run_key': 'U1', 'headcode': '1A00',
                    'direction': 'up', 'track': 'Main', 'legacy_track': 'Main',
                    'legacy_house_pass_ts': 1_010, 'house_pass_ts': 1_010,
                    'display_pass_ts': 1_010, 'movement_state': 'approaching',
                    'pass_time_source': 'td_eta',
                }
                self.assertTrue(proxy._evidence_record_snapshot([train], 1_000))
                scores = proxy._evidence_score_house_crossing('1A00', 1_020, 'Up Main')
                self.assertEqual(len(scores), 2)
                self.assertTrue(all(s['correct_headline'] for s in scores))
                self.assertEqual(scores[1]['eta_error_s'], -10)
                metrics = proxy._evidence_metrics()
                self.assertEqual(metrics['crossings_scored'], 1)
                self.assertEqual(metrics['models']['v2']['correct_rate'], 1.0)
                self.assertEqual(metrics['v2_by_source']['td_eta']['median_abs_eta_error_s'], 10)
                manual_scores = proxy._evidence_record_manual_observation({
                    'ts': 1_012, 'line': 'um', 'headcode': '1A00',
                    'predicted_ts': 1_010, 'offset_s': 2,
                    'sighted': True, 'td_berth': '1640', 'dist_mi': 0.25,
                    'confirmed': True,
                })
                self.assertEqual(len(manual_scores), 2)
                self.assertTrue(all(s['correct_headline'] for s in manual_scores))
                self.assertEqual(proxy._evidence_metrics()['manual_observations'], 1)
            finally:
                proxy.TRAIN_EVIDENCE_FILE = old_file


if __name__ == '__main__':
    unittest.main()
