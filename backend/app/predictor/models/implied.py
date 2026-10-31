"""去抽水（overround 移除）+ 分布质量评分（RPS / Brier）纯函数模块。

背景
----
竞彩 TTG 总进球盘是 8 结果盘（0/1/2/3/4/5/6/7+），存在 favorite-longshot bias
（长尾冷门档被博彩公司加更多 margin）。现状 ou_direction._implied 用朴素
multiplicative（inv/sum）归一化，未修正该偏置，会系统性低估长尾档真实概率，
进而让 p_big（大小球方向）出现可测偏差。

本模块提供多种去抽水方法（算法源自 penaltyblog, MIT，此处自实现纯 numpy/scipy，
不含其 Cython/数据类），以及用于评估分布质量的 RPS / 多分类 Brier。

去抽水方法
----------
  multiplicative : 朴素 inv/sum（= 现状），假设 margin 按比例分摊。
  power          : p_i = inv_i^k，求 k 使 Σ inv_i^k = 1（power 系数法）。
  shin           : Shin(1992/1993)，假设 margin 来自内幕交易者比例 z，
                   长尾档被过度加 margin，回推时抬高长尾。返回 z。
  logarithmic    : 在 logit 空间减去常数 c 使 Σ=1（鲁棒、常用）。

评分方法
--------
  rps              : Ranked Probability Score（有序分类，适合进球数 0~7+，越低越好）。
  brier_multiclass : 多分类 Brier（无序，越低越好）。

依赖仅 numpy / scipy，便于容器内独立运行。
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq, ridder

_EPS = 1e-12


def _as_array(odds: dict):
    """把 {k: 赔率} 转成 (keys, inv_odds 数组)。keys 升序，过滤无效赔率。"""
    keys = sorted(k for k, o in odds.items() if o is not None and o > 1.0)
    arr = np.array([1.0 / float(odds[k]) for k in keys], dtype=np.float64)
    return keys, arr


def multiplicative_probs(odds: dict):
    """朴素归一化（= 现状 ou_direction._implied 口径）。返回 {k: p} 或 None。"""
    keys, inv = _as_array(odds)
    if inv.size == 0:
        return None
    s = float(inv.sum())
    if s <= 0:
        return None
    return {k: float(v / s) for k, v in zip(keys, inv)}


def power_probs(odds: dict):
    """Power 去抽水：p_i = inv_i^k，求 k 使 Σ inv_i^k = 1。"""
    keys, inv = _as_array(odds)
    if inv.size == 0:
        return None
    s = float(inv.sum())
    if s <= 1.0 + _EPS:  # 无 overround，退化为朴素
        return {k: float(v / s) for k, v in zip(keys, inv)}

    def _err(k: float) -> float:
        return 1.0 - float(np.sum(inv ** k))

    k = float(ridder(_err, 0.0, 100.0))
    probs = inv ** k
    return {key: float(p) for key, p in zip(keys, probs)}


def shin_probs(odds: dict):
    """Shin 去抽水：margin 来自内幕交易者比例 z，回推时抬高长尾。"""
    keys, inv = _as_array(odds)
    if inv.size == 0:
        return None
    s = float(inv.sum())
    if s <= 1.0 + _EPS:
        return {k: float(v / s) for k, v in zip(keys, inv)}

    def _func(z: float) -> np.ndarray:
        return ((z ** 2 + 4.0 * (1.0 - z) * inv ** 2 / s) ** 0.5 - z) / (2.0 - 2.0 * z)

    def _err(z: float) -> float:
        return 1.0 - float(np.sum(_func(z)))

    z = float(ridder(_err, 0.0, 100.0))
    probs = _func(z)
    return {key: float(p) for key, p in zip(keys, probs)}


def logarithmic_probs(odds: dict):
    """Logarithmic 去抽水：logit 空间减常数 c 使 Σ=1。"""
    keys, inv = _as_array(odds)
    if inv.size == 0:
        return None
    s = float(inv.sum())
    if s <= 1.0 + _EPS:
        return {k: float(v / s) for k, v in zip(keys, inv)}

    probs = np.clip(inv, 1e-15, 1.0 - 1e-15)
    log_odds = np.log(probs / (1.0 - probs))

    def _err(c: float) -> float:
        adj = log_odds - c
        return float(np.sum(1.0 / (1.0 + np.exp(-adj))) - 1.0)

    try:
        c = float(brentq(_err, 0.0, 20.0))
    except ValueError:
        c = float(brentq(_err, -20.0, 20.0))
    adj = log_odds - c
    return {key: float(p) for key, p in zip(keys, 1.0 / (1.0 + np.exp(-adj)))}


def rps(probs, outcome: int) -> float:
    """Ranked Probability Score（单场，越低越好）。

    probs   : 有序概率数组（如进球数 0~7+，顺序必须与结果档位一致）。
    outcome : 实际结果档位索引（0-based）。
    """
    probs = np.asarray(probs, dtype=np.float64)
    n = len(probs)
    if n < 2:
        return 0.0
    cum_probs = np.cumsum(probs)
    cum_out = np.zeros(n, dtype=np.float64)
    cum_out[outcome] = 1.0
    cum_out = np.cumsum(cum_out)
    return float(np.sum((cum_probs - cum_out) ** 2) / (n - 1))


def brier_multiclass(probs, outcome: int) -> float:
    """多分类 Brier（单场，越低越好）。probs 为概率数组，outcome 为实际档位索引。"""
    probs = np.asarray(probs, dtype=np.float64)
    y = np.zeros_like(probs)
    y[outcome] = 1.0
    return float(np.sum((probs - y) ** 2))
