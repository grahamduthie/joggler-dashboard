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

    def test_near_house_dwelling_train_becomes_passing_not_scheduled(self):
        # Reproduces the Up Relief bug: a train dwelling at a near-house
        # platform berth (e.g. Twyford P4, td_dist_mi~0.1) for longer than
        # its own tiny remaining travel time produces a small negative ETA
        # from _berth_eta_to_house_s's age-crediting formula -- too negative
        # for the +-15/+20s 'passing' window, not old enough for 'stale'
        # (<-60s), and previously fell through to the generic 'scheduled'
        # state, which _v2_headline_eligible then discarded as too-far-past.
        train = {'display_pass_ts': 954, 'observed_pass_ts': 0,
                 'pass_time_source': 'td_eta', 'direction': 'up',
                 'td_dist_mi': 0.1, 'td_berth_age': 95, 'td_eta_s': -46}
        proxy._finalise_train_state(train, 1_000)
        self.assertEqual(train['movement_state'], 'passing')

    def test_v2_eligible_treats_passing_as_always_eligible(self):
        # Even though its own pass_ts fails the generic "within 15s" check
        # below -- the categorical state is the more trustworthy signal here.
        train = {'display_pass_ts': 954, 'movement_state': 'passing'}
        self.assertTrue(proxy._v2_headline_eligible(train, 1_000))

    def test_at_twy_platform_berth_matches_each_lines_own_platform(self):
        fresh = {'td_berth_age': 10}
        self.assertTrue(proxy._at_twy_platform_berth(
            {**fresh, 'td_berth': '1630', 'track': 'Relief', 'direction': 'up'}))
        self.assertTrue(proxy._at_twy_platform_berth(
            {**fresh, 'td_berth': '1618', 'track': 'Main', 'direction': 'up'}))
        # Wrong platform for this line/direction, or stale, or absent.
        self.assertFalse(proxy._at_twy_platform_berth(
            {**fresh, 'td_berth': '1628', 'track': 'Relief', 'direction': 'up'}))
        self.assertFalse(proxy._at_twy_platform_berth(
            {'td_berth': '1630', 'td_berth_age': 400, 'track': 'Relief', 'direction': 'up'}))
        self.assertFalse(proxy._at_twy_platform_berth({'track': 'Relief', 'direction': 'up'}))

    def test_at_twy_platform_berth_excludes_down_direction(self):
        # A Down train's house-crossing happens on arrival, BEFORE the
        # platform dwell (opposite of Up) -- one confirmed at its own
        # platform berth has normally already crossed, so this check must
        # not fire for Down even though 1637/1655 are genuinely DR/DM's own
        # platform berths (asked directly 2026-08-17; the original fix
        # wrongly applied the Up-only assumption to all four lines).
        fresh = {'td_berth_age': 10}
        self.assertFalse(proxy._at_twy_platform_berth(
            {**fresh, 'td_berth': '1637', 'track': 'Relief', 'direction': 'down'}))
        self.assertFalse(proxy._at_twy_platform_berth(
            {**fresh, 'td_berth': '1655', 'track': 'Main', 'direction': 'down'}))

    def test_finalise_state_forces_at_station_while_at_platform_berth(self):
        # A stale/coarse 'passed' claim (observed_ts already in the past)
        # must not win while the train is confirmed still at its own
        # platform berth -- this is the state-derivation half of the 9U97
        # fix; _td_enrich_trains's earlier guard is the one that stops the
        # bad observed_pass_ts/house_pass_ts from being trusted in the
        # first place (see test_td_enrich_keeps_a_stop_at_station... below).
        train = {'td_berth': '1630', 'td_berth_age': 10, 'track': 'Relief',
                 'direction': 'up', 'observed_pass_ts': 500,
                 'display_pass_ts': 500, 'pass_time_source': 'rtt_actual'}
        proxy._finalise_train_state(train, 1_000)
        self.assertEqual(train['movement_state'], 'at_station')

    def test_td_enrich_keeps_a_stop_at_station_while_still_at_its_platform(self):
        # Reproduces the actual 9U97 bug: a coarse (minute-precision) RTT
        # actual departure timestamp had already been turned into a past
        # observed_pass_ts before this function ever sees live TD data --
        # if TD still shows the train sitting in its own platform berth, that
        # premature signal must be discarded in favour of the RTT forecast/
        # schedule (a genuine TD crossing later corrects it precisely, once
        # it actually happens -- this only stops the early false claim).
        old_buffer = None
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '1642', 'to': '1630',
                                     'descr': '9U97', 'ts': 990}]
        try:
            train = {'headcode': '9U97', 'direction': 'up', 'track': 'Relief',
                     'call_type': 'STOP', 'at_station': False,
                     'observed_pass_ts': 985, 'display_pass_ts': 985,
                     'house_pass_ts': 985, 'forecast_pass_ts': 1_200,
                     'scheduled_pass_ts': 1_190, 'pass_time_source': 'rtt_actual'}
            proxy._td_enrich_trains([train], 1_000)
            self.assertTrue(train['at_station'])
            self.assertEqual(train['observed_pass_ts'], 0)
            self.assertEqual(train['house_pass_ts'], 1_200)
            self.assertEqual(train['pass_time_source'], 'rtt_forecast')
        finally:
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_td_enrich_does_not_suppress_passed_for_a_down_arrival(self):
        # A Down train genuinely at its own platform berth (1637, Down
        # Relief) has normally already crossed the house on arrival -- the
        # 9U97 guard above must not fire here and clear a real observed
        # pass just because the train is now sitting at the platform.
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '1635', 'to': '1637',
                                     'descr': '9R43', 'ts': 990}]
        try:
            train = {'headcode': '9R43', 'direction': 'down', 'track': 'Relief',
                     'call_type': 'STOP', 'at_station': True,
                     'observed_pass_ts': 985, 'display_pass_ts': 985,
                     'house_pass_ts': 985, 'forecast_pass_ts': 1_200,
                     'scheduled_pass_ts': 1_190, 'pass_time_source': 'td_crossing'}
            proxy._td_enrich_trains([train], 1_000)
            self.assertEqual(train['observed_pass_ts'], 985)
            self.assertEqual(train['house_pass_ts'], 985)
            self.assertEqual(train['pass_time_source'], 'td_crossing')
        finally:
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_held_berth_eta_cannot_beat_the_trains_own_booked_departure(self):
        # Reported live 2026-08-17: 9U87 was confirmed sitting at Reading
        # (its ORIGIN, D1/1696, 5.1 mi out) 144s after being first seen there
        # -- long enough to be flagged held -- and the constant-speed-from-
        # here math showed it passing the house in ~4 min, while its own
        # booked departure from Reading was still ~13 min away. A train
        # can't leave its origin before the booked time, so the held ETA
        # must never imply an earlier house-pass than the RTT forecast.
        old_info = proxy._berth_info
        old_pax_best = proxy._cif_pax_best
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '', 'to': '1696',
                                     'descr': '9U87', 'ts': 856}]
        try:
            proxy._berth_info = lambda area, berth: {
                'line': 'Relief', 'dist_mi': 5.1, 'dir': 'up', 'stanme': 'READNG'}
            proxy._cif_pax_best = lambda hc: None
            train = {'headcode': '9U87', 'direction': 'up', 'track': 'Relief',
                     'call_type': 'STOP', 'passenger': True, 'at_station': False,
                     'observed_pass_ts': 0, 'scheduled_pass_ts': 1_780,
                     'forecast_pass_ts': 1_780, 'house_pass_ts': 1_780}
            proxy._td_enrich_trains([train], 1_000)
            self.assertTrue(train['held'])
            self.assertEqual(train['display_pass_ts'], 1_780)
            self.assertEqual(train['pass_time_source'], 'rtt_forecast')
        finally:
            proxy._berth_info = old_info
            proxy._cif_pax_best = old_pax_best
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

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

    def test_speed_class_bucket_always_splits_freight_from_passenger(self):
        old_pax_best = proxy._cif_pax_best
        try:
            # Same power type on both sides -- must not collide into one bucket.
            proxy._cif_pax_best = lambda hc: {'power_type': 'D', 'timing_load': ''}
            freight = proxy._speed_class_bucket('4L33', False)
            passenger = proxy._speed_class_bucket('2P40', True)
            self.assertEqual(freight, 'freight_diesel')
            self.assertEqual(passenger, 'passenger_diesel')
            self.assertNotEqual(freight, passenger)
        finally:
            proxy._cif_pax_best = old_pax_best

    def test_nr_freight_hc_excludes_ecs(self):
        for hc in ('4L33', '6A01', '7B02', '8C03'):
            self.assertTrue(proxy._nr_freight_hc(hc), hc)
        self.assertFalse(proxy._nr_freight_hc('5387'))
        self.assertFalse(proxy._nr_freight_hc('1A00'))
        self.assertFalse(proxy._nr_freight_hc('2P40'))

    def test_nr_ecs_hc_matches_only_class_5(self):
        self.assertTrue(proxy._nr_ecs_hc('5387'))
        self.assertFalse(proxy._nr_ecs_hc('4L33'))
        self.assertFalse(proxy._nr_ecs_hc('1A00'))
        self.assertFalse(proxy._nr_ecs_hc(''))
        self.assertFalse(proxy._nr_ecs_hc(None))

    def test_speed_class_bucket_treats_unresolved_ecs_as_passenger_not_freight(self):
        old_pax_best = proxy._cif_pax_best
        try:
            proxy._cif_pax_best = lambda hc: None   # CIF has no schedule for this working
            self.assertEqual(proxy._speed_class_bucket('5387', None), 'passenger_other')
        finally:
            proxy._cif_pax_best = old_pax_best

    def test_td_synthesis_marks_unidentified_ecs_passenger_neither_true_nor_false(self):
        # An ECS headcode with no RTT/CIF identity (`who` empty) previously fell
        # back to hc[:1] in '129', which is False for '5' -- misreporting it as
        # freight. It should route to the passenger-side ETA speed defaults
        # without claiming it's an ordinary booked passenger service either.
        old_info = proxy._berth_info
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D6', 'from': '0596', 'to': '0592',
                                     'descr': '5Z87', 'ts': 995}]
        try:
            proxy._berth_info = lambda area, berth: {
                'line': 'Main', 'dist_mi': 3.0, 'dir': 'up', 'stanme': 'X'}
            trains = []
            proxy._td_enrich_trains(trains, 1_000)
            self.assertEqual(len(trains), 1)
            self.assertIsNone(trains[0]['passenger'])
        finally:
            proxy._berth_info = old_info
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_cif_recognised_passenger_stock_ignores_headcode_digit(self):
        old_pax_best = proxy._cif_pax_best
        try:
            proxy._cif_pax_best = lambda hc: {'timing_load': '387', 'power_type': 'EMU'}
            # '3' is not in the ordinary-passenger '129' set and is not ECS
            # ('5') either -- exactly the gap 3T60 fell into.
            self.assertTrue(proxy._cif_is_recognised_passenger_stock('3T60'))
            proxy._cif_pax_best = lambda hc: {'timing_load': '', 'power_type': 'D'}
            self.assertFalse(proxy._cif_is_recognised_passenger_stock('4L33'))
            proxy._cif_pax_best = lambda hc: None
            self.assertFalse(proxy._cif_is_recognised_passenger_stock('3T60'))
        finally:
            proxy._cif_pax_best = old_pax_best

    def test_td_synthesis_recognises_stock_class_over_unmapped_headcode_digit(self):
        # Reported live 2026-08-17: 3T60 (Reading Traincare Depot -> Paddington,
        # a genuine Class 387 EMU per CIF) showed 'passenger': False and was
        # styled/labelled as freight, purely because '3' isn't in the '129'
        # ordinary-passenger digit set and isn't ECS ('5') either.
        old_info = proxy._berth_info
        old_pax_best = proxy._cif_pax_best
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '1646', 'to': '1640',
                                     'descr': '3T60', 'ts': 995}]
        try:
            proxy._berth_info = lambda area, berth: {
                'line': 'Main', 'dist_mi': 0.25, 'dir': 'up', 'stanme': 'TWYFORD'}
            proxy._cif_pax_best = lambda hc, *a: {'timing_load': '387', 'power_type': 'EMU'}
            trains = []
            proxy._td_enrich_trains(trains, 1_000)
            self.assertEqual(len(trains), 1)
            self.assertIs(trains[0]['passenger'], True)
        finally:
            proxy._berth_info = old_info
            proxy._cif_pax_best = old_pax_best
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_speed_class_bucket_uses_timing_load_class_when_available(self):
        old_pax_best = proxy._cif_pax_best
        try:
            proxy._cif_pax_best = lambda hc: {'timing_load': '345', 'power_type': 'EMU'}
            self.assertEqual(proxy._speed_class_bucket('9U41', True), 'passenger_class345')
            proxy._cif_pax_best = lambda hc: {'timing_load': '387', 'power_type': 'EMU'}
            self.assertEqual(proxy._speed_class_bucket('1A00', True), 'passenger_class387')
        finally:
            proxy._cif_pax_best = old_pax_best

    def test_speed_class_bucket_falls_back_without_cif_data(self):
        old_pax_best = proxy._cif_pax_best
        try:
            proxy._cif_pax_best = lambda hc: None
            self.assertEqual(proxy._speed_class_bucket('1A00', True), 'passenger_other')
            self.assertEqual(proxy._speed_class_bucket('6A01', False), 'freight_other')
        finally:
            proxy._cif_pax_best = old_pax_best

    def test_speed_class_bucket_trusts_recognised_class_over_headcode_prefix(self):
        # A 5xxx ECS move of a Class 387 unit reads as freight by headcode
        # prefix alone, but it's the same physical unit as its passenger
        # workings -- CIF's own class number must win so the speed learner
        # doesn't average a fast EMU's samples into the freight bucket.
        old_pax_best = proxy._cif_pax_best
        try:
            proxy._cif_pax_best = lambda hc: {'timing_load': '387', 'power_type': 'EMU'}
            self.assertEqual(proxy._speed_class_bucket('5387', False), 'passenger_class387')
            self.assertEqual(
                proxy._speed_class_bucket('5387', False),
                proxy._speed_class_bucket('2P44', True))
        finally:
            proxy._cif_pax_best = old_pax_best

    def test_lookup_speed_mph_prefers_learned_over_cif_over_constant(self):
        old_pax_best = proxy._cif_pax_best
        with proxy._chain_lock:
            old_class_speed = dict(proxy._ca_class_speed)
            proxy._ca_class_speed.clear()
        try:
            proxy._cif_pax_best = lambda hc: None
            self.assertEqual(proxy._lookup_speed_mph('9Z99', True, 'Main'), 90.0)
            self.assertEqual(proxy._lookup_speed_mph('9Z99', False, 'Relief'), 35.0)

            proxy._cif_pax_best = lambda hc: {
                'speed': '110', 'timing_load': '', 'power_type': ''}
            self.assertEqual(proxy._lookup_speed_mph('1A00', True, 'Main'), 110.0)

            # Below the sample-count floor: learned value must not be trusted yet.
            with proxy._chain_lock:
                proxy._ca_class_speed[('passenger_other', 'Main')] = [
                    102.5, proxy._CLASS_SPEED_MIN_N - 1]
            self.assertEqual(proxy._lookup_speed_mph('1A00', True, 'Main'), 110.0)

            # At the floor: learned value now wins over CIF.
            with proxy._chain_lock:
                proxy._ca_class_speed[('passenger_other', 'Main')] = [
                    102.5, proxy._CLASS_SPEED_MIN_N]
            self.assertEqual(proxy._lookup_speed_mph('1A00', True, 'Main'), 102.5)
        finally:
            proxy._cif_pax_best = old_pax_best
            with proxy._chain_lock:
                proxy._ca_class_speed.clear()
                proxy._ca_class_speed.update(old_class_speed)

    def test_berth_eta_to_house_uses_cif_speed_when_available(self):
        old_info = proxy._berth_info
        old_pax_best = proxy._cif_pax_best
        with proxy._chain_lock:
            old_class_speed = dict(proxy._ca_class_speed)
            proxy._ca_class_speed.clear()
        try:
            proxy._berth_info = lambda area, berth: {'line': 'Main', 'dist_mi': 2.0}
            proxy._cif_pax_best = lambda hc: None
            eta_default, _ = proxy._berth_eta_to_house_s(
                'D6', 'X', 'up', True, True, 0, headcode='1A00')
            proxy._cif_pax_best = lambda hc: {
                'speed': '125', 'timing_load': '', 'power_type': ''}
            eta_cif, _ = proxy._berth_eta_to_house_s(
                'D6', 'X', 'up', True, True, 0, headcode='1A00')
            # 125 mph (CIF) is faster than the 90 mph default -> shorter ETA.
            self.assertGreater(eta_default, eta_cif)
        finally:
            proxy._berth_info = old_info
            proxy._cif_pax_best = old_pax_best
            with proxy._chain_lock:
                proxy._ca_class_speed.clear()
                proxy._ca_class_speed.update(old_class_speed)

    def test_ca_observe_class_speed_folds_a_plausible_sample_into_the_learner(self):
        old_info = proxy._berth_info
        old_pax_best = proxy._cif_pax_best
        with proxy._chain_lock:
            old_class_speed = dict(proxy._ca_class_speed)
            proxy._ca_class_speed.clear()
        try:
            proxy._berth_info = lambda area, berth: (
                {'line': 'Main', 'dist_mi': 1.0} if berth == 'A'
                else {'line': 'Main', 'dist_mi': 0.0})
            proxy._cif_pax_best = lambda hc: None
            proxy._ca_observe_class_speed('D6', 'A', 'B', '1A00', 40)  # 1.0 mi / 40s = 90mph
            with proxy._chain_lock:
                mph, n = proxy._ca_class_speed[('passenger_other', 'Main')]
            self.assertAlmostEqual(mph, 90.0, delta=0.5)
            self.assertEqual(n, 1)
        finally:
            proxy._berth_info = old_info
            proxy._cif_pax_best = old_pax_best
            with proxy._chain_lock:
                proxy._ca_class_speed.clear()
                proxy._ca_class_speed.update(old_class_speed)

    def test_ca_observe_class_speed_rejects_an_implausible_sample(self):
        old_info = proxy._berth_info
        with proxy._chain_lock:
            old_class_speed = dict(proxy._ca_class_speed)
            proxy._ca_class_speed.clear()
        try:
            proxy._berth_info = lambda area, berth: (
                {'line': 'Main', 'dist_mi': 1.0} if berth == 'A'
                else {'line': 'Main', 'dist_mi': 0.0})
            proxy._ca_observe_class_speed('D6', 'A', 'B', '1A00', 2)  # 1 mi / 2s = 1800mph
            with proxy._chain_lock:
                self.assertEqual(proxy._ca_class_speed, {})
        finally:
            proxy._berth_info = old_info
            with proxy._chain_lock:
                proxy._ca_class_speed.clear()
                proxy._ca_class_speed.update(old_class_speed)

    def test_ca_observe_end_to_end_updates_class_speed_without_deadlock(self):
        old_info = proxy._berth_info
        old_pax_best = proxy._cif_pax_best
        with proxy._chain_lock:
            old_succ = {k: dict(v) for k, v in proxy._ca_succ.items()}
            old_pred = {k: dict(v) for k, v in proxy._ca_pred.items()}
            old_transit = dict(proxy._ca_transit)
            old_last_pos = dict(proxy._ca_last_pos)
            old_class_speed = dict(proxy._ca_class_speed)
            proxy._ca_last_pos.clear()
            proxy._ca_class_speed.clear()
        try:
            proxy._berth_info = lambda area, berth: (
                {'line': 'Main', 'dist_mi': 1.0} if berth == 'A'
                else {'line': 'Main', 'dist_mi': 0.0})
            proxy._cif_pax_best = lambda hc: None
            proxy._ca_observe('D6', '', 'A', '9Z50', 1_000)   # first sighting, no prior pos
            proxy._ca_observe('D6', 'A', 'B', '9Z50', 1_040)  # steps A->B 40s later
            with proxy._chain_lock:
                self.assertIn(('passenger_other', 'Main'), proxy._ca_class_speed)
        finally:
            proxy._berth_info = old_info
            proxy._cif_pax_best = old_pax_best
            with proxy._chain_lock:
                proxy._ca_succ.clear(); proxy._ca_succ.update(old_succ)
                proxy._ca_pred.clear(); proxy._ca_pred.update(old_pred)
                proxy._ca_transit.clear(); proxy._ca_transit.update(old_transit)
                proxy._ca_last_pos.clear(); proxy._ca_last_pos.update(old_last_pos)
                proxy._ca_class_speed.clear(); proxy._ca_class_speed.update(old_class_speed)

    def test_chain_persistence_round_trips_class_speed(self):
        old_file = proxy._CHAIN_FILE
        with proxy._chain_lock:
            old_class_speed = dict(proxy._ca_class_speed)
            proxy._ca_class_speed.clear()
            proxy._ca_class_speed[('passenger_class387', 'Relief')] = [58.2, 12]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                proxy._CHAIN_FILE = str(pathlib.Path(tmp) / 'berth_chain.json')
                proxy._save_chain()
                with proxy._chain_lock:
                    proxy._ca_class_speed.clear()
                proxy._load_chain()
                with proxy._chain_lock:
                    self.assertEqual(
                        proxy._ca_class_speed.get(('passenger_class387', 'Relief')),
                        [58.2, 12])
        finally:
            proxy._CHAIN_FILE = old_file
            with proxy._chain_lock:
                proxy._ca_class_speed.clear()
                proxy._ca_class_speed.update(old_class_speed)

    def test_td_position_cutoff_keeps_a_held_train_within_600s(self):
        # A train held at a red signal doesn't step berths, so its last CA
        # message ages past any short cutoff even though it's still there
        # (see the comment on cutoff_pos). 450s: within the 600s the
        # synthesis loop is meant to tolerate, but past the old 300s cutoff
        # that used to discard it before that tolerance ever applied.
        old_info = proxy._berth_info
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D6', 'from': '1608', 'to': '1604',
                                     'descr': '9Z99', 'ts': 550}]
        try:
            proxy._berth_info = lambda area, berth: {
                'line': 'Main', 'dist_mi': 2.0, 'dir': 'up', 'stanme': 'X'}
            skip_log = []
            trains = []
            proxy._td_enrich_trains(trains, 1_000, skip_log=skip_log)
            self.assertEqual(skip_log, [])
            self.assertEqual(len(trains), 1)
            self.assertEqual(trains[0]['headcode'], '9Z99')
        finally:
            proxy._berth_info = old_info
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_fixes_older_than_the_position_cutoff_are_silently_absent(self):
        # A position older than cutoff_pos (600s) never enters td_pos at all,
        # so it's invisible even to skip_log -- not a "reason", just absence.
        # Documents the current known boundary of what this diagnostic can see.
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D6', 'from': '1608', 'to': '1604',
                                     'descr': '9Z99', 'ts': 100}]
        try:
            skip_log = []
            proxy._td_enrich_trains([], 1_000, skip_log=skip_log)
            self.assertEqual(skip_log, [])
        finally:
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_td_synthesis_skip_log_explains_outside_corridor(self):
        old_info = proxy._berth_info
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D6', 'from': '0400', 'to': '0390',
                                     'descr': '9Z98', 'ts': 995}]
        try:
            proxy._berth_info = lambda area, berth: {
                'line': 'Main', 'dist_mi': 10.0, 'dir': 'up', 'stanme': 'FAR'}
            skip_log = []
            proxy._td_enrich_trains([], 1_000, skip_log=skip_log)
            self.assertEqual(len(skip_log), 1)
            self.assertEqual(skip_log[0]['reason'], 'outside_corridor')
        finally:
            proxy._berth_info = old_info
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_light_locomotive_is_no_longer_excluded_from_corridor_synthesis(self):
        # Reported live 2026-08-17: 0Z47 (a light-engine move, headcode class
        # '0') physically passed the house and never appeared -- '0' used to
        # be excluded from corridor synthesis bundled with the Henley branch
        # (2H) exclusion, on the wrong assumption that both never reach the
        # main corridor. A light engine genuinely can run on the main lines,
        # unlike a Henley shuttle.
        old_info = proxy._berth_info
        old_pax_best = proxy._cif_pax_best
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '1646', 'to': '1640',
                                     'descr': '0Z47', 'ts': 995}]
        try:
            proxy._berth_info = lambda area, berth: {
                'line': 'Main', 'dist_mi': 0.25, 'dir': 'up', 'stanme': 'TWYFORD'}
            proxy._cif_pax_best = lambda hc, *a: None
            skip_log = []
            trains = []
            proxy._td_enrich_trains(trains, 1_000, skip_log=skip_log)
            self.assertEqual(skip_log, [])
            self.assertEqual(len(trains), 1)
            self.assertEqual(trains[0]['headcode'], '0Z47')
        finally:
            proxy._berth_info = old_info
            proxy._cif_pax_best = old_pax_best
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_henley_branch_is_still_excluded_from_corridor_synthesis(self):
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '1636', 'to': '1632',
                                     'descr': '2H37', 'ts': 995}]
        try:
            skip_log = []
            proxy._td_enrich_trains([], 1_000, skip_log=skip_log)
            self.assertEqual(len(skip_log), 1)
            self.assertEqual(skip_log[0]['reason'], 'henley_branch')
        finally:
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_is_real_headcode_matches_only_the_digit_letter_digit_digit_shape(self):
        for hc in ('9U87', '0Z47', '3T60', '2P44', '5N82', '0B00', '2H37'):
            self.assertTrue(proxy._is_real_headcode(hc), hc)
        for bad in ('CAMS', 'CMAS', '0000', '', '9U8', '9U877', 'AB12'):
            self.assertFalse(proxy._is_real_headcode(bad), bad)

    def test_non_headcode_td_descriptor_excluded_from_corridor_synthesis_only(self):
        # Reported live 2026-08-17: 'CAMS' appeared at Reading P13 (D1/1694),
        # not a real headcode -- no digit at all, and confirmed by its own
        # data shape: no 'from' berth (an interpose, not a real step) and no
        # resolvable running line. Must not become a predicted "train" with a
        # house_pass_ts that could show up on "next past the house" -- but
        # this gate lives only in corridor synthesis (which builds that
        # prediction), not at TD ingestion or /api/td-live, since the user
        # wants to keep seeing odd descriptors on the berth panel itself.
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '', 'to': '1694',
                                     'descr': 'CAMS', 'ts': 995}]
        try:
            skip_log = []
            trains = []
            proxy._td_enrich_trains(trains, 1_000, skip_log=skip_log)
            self.assertEqual(trains, [])
            self.assertEqual(len(skip_log), 1)
            self.assertEqual(skip_log[0]['reason'], 'not_a_headcode')
        finally:
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_rail_replacement_bus_is_excluded_from_corridor_synthesis(self):
        # A rail-replacement bus (headcode '0B...') is a road vehicle: RTT
        # lists it as a bookable public service, but it can never produce a
        # real TD sighting. This is a belt-and-braces check -- the live bug
        # (0B00 showing on the approach list with an ETA that could never
        # resolve) was actually in the RTT-predictor loop, not here.
        with proxy._td_lock:
            old_buffer = list(proxy._td_buffer)
            proxy._td_buffer[:] = [{'area': 'D1', 'from': '1646', 'to': '1640',
                                     'descr': '0B00', 'ts': 995}]
        try:
            skip_log = []
            proxy._td_enrich_trains([], 1_000, skip_log=skip_log)
            self.assertEqual(len(skip_log), 1)
            self.assertEqual(skip_log[0]['reason'], 'rail_replacement_bus')
        finally:
            with proxy._td_lock:
                proxy._td_buffer[:] = old_buffer

    def test_row_candidates_includes_ineligible_trains_with_a_reason_visible(self):
        trains = [
            {'uid': 'winner', 'run_key': 'winner', 'direction': 'up', 'track': 'Main',
             'legacy_track': 'Main', 'legacy_house_pass_ts': 1_010,
             'house_pass_ts': 1_010, 'pass_time_source': 'td_eta',
             'movement_state': 'approaching'},
            {'uid': 'too_old', 'run_key': 'too_old', 'direction': 'up', 'track': 'Main',
             'legacy_track': 'Main', 'legacy_house_pass_ts': 500,
             'house_pass_ts': 500, 'pass_time_source': 'schedule',
             'movement_state': 'stale'},
        ]
        candidates = proxy._row_candidates(trains, 1_000, 'legacy')
        self.assertEqual(len(candidates['um']), 2)
        by_uid = {c['uid']: c for c in candidates['um']}
        self.assertTrue(by_uid['winner']['eligible'])
        self.assertFalse(by_uid['too_old']['eligible'])
        self.assertEqual(candidates['ur'], [])

    def test_evidence_snapshot_carries_candidates_and_td_unmatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_file = proxy.TRAIN_EVIDENCE_FILE
            proxy.TRAIN_EVIDENCE_FILE = str(pathlib.Path(tmp) / 'evidence.jsonl')
            with proxy._evidence_lock:
                proxy._evidence_recent.clear()
                proxy._evidence_last_signature = ''
                proxy._evidence_last_record_ts = 0
            try:
                train = {'uid': 'U1', 'run_key': 'U1', 'headcode': '1A00',
                         'direction': 'up', 'track': 'Main', 'legacy_track': 'Main',
                         'legacy_house_pass_ts': 1_010, 'house_pass_ts': 1_010,
                         'display_pass_ts': 1_010, 'movement_state': 'approaching',
                         'pass_time_source': 'td_eta'}
                proxy._evidence_record_snapshot(
                    [train], 1_000,
                    td_unmatched=[{'headcode': '9Z97', 'area': 'D6', 'berth': '0400',
                                   'reason': 'outside_corridor'}])
                recorded = proxy._evidence_recent[-1]
                self.assertIn('um', recorded['candidates']['legacy'])
                self.assertEqual(recorded['candidates']['legacy']['um'][0]['headcode'], '1A00')
                self.assertEqual(recorded['td_unmatched'][0]['headcode'], '9Z97')
            finally:
                proxy.TRAIN_EVIDENCE_FILE = old_file

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
