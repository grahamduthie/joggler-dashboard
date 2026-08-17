/* Shared, dependency-free train-row state helpers for /trains and /now.
   The backend owns operational state; this file deliberately contains only
   presentation eligibility and a backwards-compatible fallback for an older
   API response during a rolling deployment. */
(function (global) {
  'use strict';

  const OBSERVED_GRACE_MS = 20000;

  function passMs(t) {
    const ts = t && (t.display_pass_ts || t.house_pass_ts);
    if (ts) return Number(ts) * 1000;
    if (!t || !t.twy_sched) return 0;
    let ms = new Date(t.twy_sched).getTime();
    if (!t.twy_actual && t.late_min > 0) ms += t.late_min * 60000;
    return ms;
  }

  function trackKey(t) {
    if (!t || !t.direction || !t.track) return null;
    return (t.direction === 'up' ? 'u' : 'd') + (t.track === 'Main' ? 'm' : 'r');
  }

  function isHeadlineEligible(t, now) {
    if (!t || t.cancelled || t.movement_state === 'stale') return false;
    const ms = passMs(t);
    if (!ms) return false;
    if (t.movement_state === 'passed') return now - ms <= OBSERVED_GRACE_MS;
    if (t.movement_state === 'at_station' || t.movement_state === 'held' ||
        t.movement_state === 'held_at_red') return true;
    if (t.movement_state === 'passing' || t.movement_state === 'approaching' ||
        t.movement_state === 'forecast' || t.movement_state === 'scheduled') {
      // The backend will correct stale state on its next poll. Do not let a
      // cached prediction monopolise a row for the old 45–100 second grace.
      return ms >= now - 15000;
    }
    // Compatibility behaviour for a temporarily older backend response.
    const grace = t.twy_actual ? (t.direction === 'up' ? 45000 : 20000) : 100000;
    return ms >= now - grace;
  }

  function compareCandidates(a, b) {
    // A physically observed/passing train only remains eligible for its short
    // grace. All other viable candidates are ordered by expected house time.
    return passMs(a) - passMs(b);
  }

  function countdown(t, now) {
    const state = t && t.movement_state;
    const diff = passMs(t) - now;
    if (t && t.cancelled) return {html:'CANC', cls:'gone', passing:false};
    if (state === 'at_station') return {html:'AT STATION', cls:'stn', passing:false};
    if (state === 'held_at_red') return {html:'HELD AT RED', cls:'held', passing:false};
    if (state === 'held') return {html:'HELD', cls:'held', passing:false};
    if (state === 'passed') return {html:'PASSED', cls:'gone', passing:false};
    if (state === 'passing') return {html:'NOW', cls:'now', passing:true};
    if (state) {
      if (diff <= 0) return {html:'PASSED', cls:'gone', passing:false};
      if (diff < 90000) return {html:Math.max(1, Math.round(diff / 1000)) + '<span class="unit">sec</span>', cls:'', passing:false};
      return {html:Math.round(diff / 60000) + '<span class="unit">min</span>', cls:'', passing:false};
    }
    // Compatibility fallback while backend/frontend are rolled independently.
    if (diff <= 25000 && diff > -45000) return {html:'NOW', cls:'now', passing:true};
    if (diff <= 0) return {html:'PASSED', cls:'gone', passing:false};
    if (diff < 90000) return {html:Math.max(1, Math.round(diff / 1000)) + '<span class="unit">sec</span>', cls:'', passing:false};
    return {html:Math.round(diff / 60000) + '<span class="unit">min</span>', cls:'', passing:false};
  }

  global.TrainDisplay = {passMs, trackKey, isHeadlineEligible, compareCandidates, countdown,
                         OBSERVED_GRACE_MS};
}(window));
