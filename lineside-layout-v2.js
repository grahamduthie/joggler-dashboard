/* Display-only geometry shadow configuration.  The normal /lineside URL
 * retains v1; only /lineside?layout=v2 reads this reviewed override. */
window.LINESIDE_LAYOUT_V2 = Object.freeze({
  id: 'v2',
  reading: Object.freeze({
    // The learner proves these are parallel Reading platform/throat roads:
    // they converge on the four approach anchors below, not on each other.
    removeFromRunningLine: Object.freeze([
      '1694', '1698', '1696', '1690', '1688', '1695', '1693', '1697', '1685'
    ]),
    stationBerthsAdd: Object.freeze([
      '1694', '1698', '1696', '1690', '1688', '1695', '1693', '1697', '1685'
    ]),
    approachAnchors: Object.freeze({
      ur: '1676', um: '1672', dr: '1687', dm: '1675'
    })
  }),
  // 1679 is the sparse, long-dwell branch; 1669 is normal DR through flow.
  runningLineExclusions: Object.freeze(['1679']),
  sidings: Object.freeze([Object.freeze({
    id: 'kennet-loop-siding',
    berths: Object.freeze(['1679']),
    fromBerth: '1669', toBerth: '1687', line: 'dr', offsetY: -28
  })]),
  // Review columns, not a distance survey.  They preserve the verified four-line
  // alignments/crossovers and keep the house immediately east of Twyford.
  berthX: Object.freeze({
    '1676':150, '1687':150, '1672':150, '1675':150,
    '1668':225, '1677':225, '1666':225, '1667':225,
    '1664':300, '1669':300, '1662':300, '1663':300,
    '1660':345, '1665':345, '1658':345,
    '1652':390, '1659':390, '1650':390,
    '1648':420, '1661':400,
    '1644':450, '1657':440,
    '1642':480, '1646':480, '1640':570,
    // Twyford is staggered by platform/line; the house is at x=676.
    '1630':624, '1637':624, '1626':646, '1655':650,
    // First sections east of Twyford line up at the house-side boundary.
    '1628':690, '1635':690, '1633':690,
    '1624':730, '1629':730,
    // Verified Ruscombe crossovers: UR/UM and DR/DM respectively.
    '1622':770, '1618':770, '1631':760, '1625':770,
    '1614':830, '1610':860,
    '1620':810, '1627':830, '1621':810,
    '1616':850, '1623':870, '1609':850,
    '1612':890, '1611':910, '1606':890, '1605':900,
    '1608':930, '1607':950, '1602':930, '1601':950,
    '1604':970, '1603':990
  }),
  geometryRules: Object.freeze({
    crossovers: 'track_gaps_diagonal_only',
    turnbackLeads: 'extend_to_turnback_berth'
  })
});
