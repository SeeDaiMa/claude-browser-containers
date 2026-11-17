/** 订阅节点的标称国家／地区，只从名称识别，不代表实测出口 IP 归属。 */
type NodeCountry = { code: string; label: string }

const codes = `
  AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
  CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
  GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP
  KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS
  MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS
  RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW
  TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
`
  .trim()
  .split(/\s+/)
const labels = new Intl.DisplayNames(['zh-CN'], { type: 'region', style: 'short' })
const names = ['zh-CN', 'zh-TW', 'en'].map(
  (locale) => new Intl.DisplayNames([locale], { type: 'region' }),
)
const aliases: Record<string, string[]> = {
  GB: ['UK', 'Great Britain', 'Britain', 'England'],
  US: ['USA', 'U.S.A.', 'U.S.', 'America'],
  HK: ['香港', 'HongKong'],
  MO: ['澳门', '澳門', 'Macau'],
  TW: ['台湾', '台灣', '臺灣', '中国台湾', '中國台灣', '中國臺灣', 'Taiwan'],
  RU: ['Russia'],
  VN: ['Vietnam'],
  TR: ['Turkey'],
  LA: ['Laos'],
  AE: ['阿联酋', '阿聯酋', 'UAE'],
}
const countries = new Map<string, NodeCountry>(
  codes.map((code) => [code, { code, label: labels.of(code) || code }]),
)
const matches = codes
  .flatMap((code) => {
    const country = countries.get(code)!
    const terms = new Set([
      country.label,
      ...names.map((name) => name.of(code) || ''),
      ...(aliases[code] || []),
    ])
    return [...terms].filter(Boolean).map((term) => {
      const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      // 英文名称前后不能连着字母；允许紧接节点编号或中文后缀。
      const pattern = /[a-z]/i.test(term) ? `(?<![a-z])${escaped}(?![a-z])` : escaped
      return { country, term, pattern: new RegExp(pattern, 'i') }
    })
  })
  .sort((a, b) => b.term.length - a.term.length)

export function countryFromNodeName(name: string): NodeCountry | null {
  const flag = name.match(/[\u{1F1E6}-\u{1F1FF}]{2}/u)?.[0]
  if (flag) {
    const code = [...flag]
      .map((letter) => String.fromCharCode(letter.codePointAt(0)! - 0x1f1e6 + 65))
      .join('')
    const country = countries.get(code)
    if (country) return country
  }
  return matches.find((match) => match.pattern.test(name))?.country || null
}
