const ACTIVITY_NAME_MAP = {
  run: '跑步',
  running: '跑步',
  'track run': '跑道跑步',
  'track running': '跑道跑步',
  'trail run': '越野跑',
  'road bike': '公路骑行',
  cycling: '骑行',
  'indoor cycling': '室内骑行',
  'mountain bike': '山地骑行',
  'strength training': '力量训练',
  workout: '力量训练',
  swim: '游泳',
  'open water swim': '公开水域游泳',
  hike: '徒步',
  walk: '步行',
  yoga: '瑜伽',
  treadmill: '跑步机',
  'sport 1002': '跑步',
}

/** 归一化 COROS 活动类型，避免在中文面板直接暴露英文名称。 */
export function activityNameInChinese(name) {
  const label = String(name || '').trim()
  if (!label) return '未知运动'
  if (/[^\x00-\x7F]/.test(label)) return label

  const normalized = label.toLowerCase()
  if (ACTIVITY_NAME_MAP[normalized]) return ACTIVITY_NAME_MAP[normalized]
  if (normalized.includes('trail') && normalized.includes('run')) return '越野跑'
  if (normalized.includes('track') && normalized.includes('run')) return '跑道跑步'
  if (normalized.includes('run')) return '跑步'
  if (normalized.includes('bike') || normalized.includes('cycl')) return '骑行'
  if (normalized.includes('swim')) return '游泳'
  if (normalized.includes('strength') || normalized.includes('workout')) return '力量训练'
  if (normalized.includes('walk')) return '步行'
  return '运动训练'
}

/** 读取最新负荷所在日期的比例，避免拼接不同日期的两个指标。 */
export function latestLoadRecord(records) {
  const record = [...records]
    .sort((left, right) => right.date.localeCompare(left.date))
    .find(item => Number.isFinite(item.training_load))
  if (!record) return { load: null, ratio: null }
  return {
    load: record.training_load,
    ratio: Number.isFinite(record.training_load_ratio) ? record.training_load_ratio : null,
  }
}
