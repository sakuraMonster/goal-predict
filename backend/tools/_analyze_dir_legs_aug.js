// 方案D + 方案C 方向腿 8月选择命中率测算（只读：拉取线上接口输出，聚合逐日已选方向腿）
// 方向腿定义：方案D = kind==="dir" 的腿（正路池 favorite_hafu 胜胜/负负、模糊池 ambiguous_had fav）
//            方案C = source!=="hafu" 的胜平负腿（模糊池 ambiguous_hafu→fav / 冷门池 had_upset）
const fs = require("fs");
const path = require("path");

const BASE = "http://localhost:8010/api/market-flow/predictions/";
const QS = "start_date=2026-08-01&end_date=2026-08-31";

const fmt = (n, d) => (n ? (n / d).toFixed(4) : "-");

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function pct(hit, n) {
  return `${hit}/${n} p=${fmt(hit, n)}`;
}

async function main() {
  const planD = await fetchJson(BASE + "parlay-d?" + QS);
  const planC = await fetchJson(BASE + "parlay-dir?" + QS);

  const lines = [];
  lines.push("===== 方案D + 方案C · 方向腿 8月选择命中率（2026-08-01 ~ 08-31 已结算输出）=====");
  lines.push(`窗口: ${planD.window_start} ~ ${planD.window_end}`);

  // ---------- 方案D：方向腿（kind=dir） ----------
  const dDir = [];
  for (const p of planD.picks) {
    for (const lg of p.legs) {
      if (lg.kind === "dir") dDir.push({ ...lg, matchday: p.matchday, combo_level: p.combo_level, pickHit: p.hit });
    }
  }
  const dSettled = dDir.filter((l) => l.hit !== null);
  lines.push("\n========== 方案D（1进球+1半全场+1方向） ==========");
  lines.push(`出串=${planD.picks.length}天  串关命中 ${planD.stats.hit}/${planD.stats.n} p_hit=${fmt(planD.stats.hit, planD.stats.n)}（参考）`);
  lines.push(`方向腿: n=${dSettled.length} 命中=${dSettled.filter((l) => l.hit).length} ${pct(dSettled.filter((l) => l.hit).length, dSettled.length)}`);
  for (const src of ["favorite_hafu", "ambiguous_had"]) {
    const g = dSettled.filter((l) => l.source === src);
    if (!g.length) continue;
    const h = g.filter((l) => l.hit).length;
    lines.push(`  source=${src}: n=${g.length} 命中=${h} ${pct(h, g.length)}  均p_hat=${(g.reduce((s, l) => s + l.p_hat, 0) / g.length).toFixed(4)} 均赔=${(g.reduce((s, l) => s + l.odds, 0) / g.length).toFixed(3)}`);
  }
  lines.push("  逐日:");
  for (const p of planD.picks) {
    const lg = p.legs.find((l) => l.kind === "dir");
    if (!lg || lg.hit === null) continue;
    lines.push(`  [${p.matchday}] ${lg.home_team}vs${lg.away_team} ${lg.source} 选${lg.pick}@${lg.odds} p_hat=${lg.p_hat} -> ${lg.hit ? "命中" : "未中"}`);
  }

  // ---------- 方案C：胜平负(方向)腿（source!=hafu） ----------
  const cDir = [];
  for (const p of planC.picks) {
    for (const lg of p.legs) {
      if (lg.source !== "hafu") cDir.push({ ...lg, matchday: p.matchday, pickHit: p.hit });
    }
  }
  const cSettled = cDir.filter((l) => l.hit !== null);
  lines.push("\n========== 方案C（1半全场+1胜平负/方向） ==========");
  lines.push(`出串=${planC.picks.length}天  串关命中 ${planC.stats.hit}/${planC.stats.n} p_hit=${fmt(planC.stats.hit, planC.stats.n)}（参考）`);
  lines.push(`胜平负(方向)腿: n=${cSettled.length} 命中=${cSettled.filter((l) => l.hit).length} ${pct(cSettled.filter((l) => l.hit).length, cSettled.length)}`);
  const cGroups = [
    ["ambiguous_had(模糊正路)", (l) => l.source === "ambiguous_had"],
    ["had_upset(强冷门)", (l) => l.source === "had_upset" && !l.fallback],
    ["had_upset(冷门兜底)", (l) => l.source === "had_upset" && !!l.fallback],
  ];
  for (const [name, cond] of cGroups) {
    const g = cSettled.filter(cond);
    if (!g.length) continue;
    const h = g.filter((l) => l.hit).length;
    lines.push(`  ${name}: n=${g.length} 命中=${h} ${pct(h, g.length)}  均p_hat=${(g.reduce((s, l) => s + l.p_hat, 0) / g.length).toFixed(4)} 均赔=${(g.reduce((s, l) => s + l.odds, 0) / g.length).toFixed(3)}`);
  }
  lines.push("  逐日:");
  for (const p of planC.picks) {
    const lg = p.legs.find((l) => l.source !== "hafu");
    if (!lg || lg.hit === null) continue;
    const tag = lg.fallback ? "兜底" : lg.source === "ambiguous_had" ? "模糊正路" : "强冷门";
    lines.push(`  [${p.matchday}] ${lg.home_team}vs${lg.away_team} ${lg.source}${lg.fallback ? "(兜底)" : ""} 选${lg.pick}@${lg.odds} p_hat=${lg.p_hat} -> ${lg.hit ? "命中" : "未中"}`);
  }

  // ---------- 合并视角 ----------
  lines.push("\n========== 合并（D方向腿 + C胜平负腿） ==========");
  const all = [...dSettled, ...cSettled];
  const ah = all.filter((l) => l.hit).length;
  lines.push(`合计: n=${all.length} 命中=${ah} ${pct(ah, all.length)}`);

  // 去重：同一比赛日 同一场(队名) 同一选择 只算一次（两方案同时选中同一注方向）
  const key = (l) => `${l.matchday}|${l.home_team}|${l.away_team}|${l.pick}`;
  const seen = new Set();
  const uniq = [];
  for (const l of all) {
    const k = key(l);
    if (!seen.has(k)) {
      seen.add(k);
      uniq.push(l);
    }
  }
  const uh = uniq.filter((l) => l.hit).length;
  lines.push(`去重(同场同选只算1次): n=${uniq.length} 命中=${uh} ${pct(uh, uniq.length)}`);

  // 两方案当日是否选出同一注方向
  const dMap = new Map(dSettled.map((l) => [key(l), l]));
  const cMap = new Map(cSettled.map((l) => [key(l), l]));
  const agree = cSettled.filter((l) => dMap.has(key(l)));
  lines.push(`D与C同日同场同选同一方向腿: ${agree.length} 条（若同注则命中状态一致，检查一致=${agree.filter((l) => l.hit === dMap.get(key(l)).hit).length}/${agree.length}）`);
  const dOnly = dSettled.filter((l) => !cMap.has(key(l)));
  const cOnly = cSettled.filter((l) => !dMap.has(key(l)));
  const dOh = dOnly.filter((l) => l.hit).length;
  const cOh = cOnly.filter((l) => l.hit).length;
  lines.push(`仅方案D选: n=${dOnly.length} 命中=${dOh} ${pct(dOh, dOnly.length)}`);
  lines.push(`仅方案C选: n=${cOnly.length} 命中=${cOh} ${pct(cOh, cOnly.length)}`);

  const outPath = path.join(__dirname, "_dir_legs_aug.txt");
  fs.writeFileSync(outPath, lines.join("\n"), "utf8");
  console.log(lines.join("\n"));
  console.log("\nreport ->", outPath);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
