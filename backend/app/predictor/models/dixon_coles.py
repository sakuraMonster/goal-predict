"""Dixon-Coles 双变量泊松进球数模型（足球数学标准范式）。

与系统现有进球模型彻底切割：
  - Model B  Zero-Inflated Poisson 回归（黑箱特征回归）
  - Model C  市场 + 基本面启发式融合（手写权重 + 联赛参数包）
  - Model D  手写攻防比率法（硬编码联赛基线、RHO 常量、无 MLE、无时间衰减）

本模型实现 Dixon & Coles (1997) 原文 + Maher (1982) 独立泊松的核心：

    λ_home = exp(mu + attack[home] + defense[away] + home_adv)
    λ_away = exp(mu + attack[away] + defense[home])

  - attack_t  球队 t 的进攻强度（越高越能进球）
  - defense_t 球队 t 的防守弱点（越高越易失球）
  - home_adv  主场优势（常数）
  - mu        整体进球水平基线

方法要点（对应 football-mathematics-framework 的共识）：
  1. 泊松假设（镜片 3）：进球服从泊松，由攻防强度分解（Maher 独立泊松）。
  2. 低分相关性修正 ρ（Dixon-Coles τ 函数）：修正 0-0 / 1-0 / 0-1 / 1-1 的独立性偏差，
     即「承认比分存在节奏联动，而非完全独立」。
  3. 时间衰减 ξ（镜片 1 的样本量问题）：近期比赛权重更高，exp(-ξ·Δt) 衰减，
     缓解「赛季初样本少 / 球队状态漂移」。
  4. L2 正则（镜片 1 的诚实边界）：低样本球队（中位仅 4 场）收缩到联赛均值，
     避免把噪声当信号（反模式 2）。
  5. 纯基本面，不含市场赔率；市场作为可选的「后融合」由调用方完成（见回测脚本）。

依赖仅 numpy / scipy（无 app 内依赖、无 pandas），便于在容器内独立运行。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

MAX_GOALS = 10  # 单队比分截断（比分矩阵维度 = MAX_GOALS+1）


def _logfact_upto(n: int) -> np.ndarray:
    """预计算 log(k!) for k=0..n"""
    return gammaln(np.arange(n + 1) + 1.0)


class DixonColes:
    """双变量泊松攻防强度模型（Dixon-Coles）。

    用法：
        dc = DixonColes(xi=0.003, l2_reg=0.5)
        dc.fit(home_team, away_team, home_goals, away_goals, dates, ref_date=ref_date)
        out = dc.predict(home_team_id, away_team_id)
    """

    def __init__(
        self,
        xi: float = 0.003,
        l2_reg: float = 0.5,
        rho_bounds: Tuple[float, float] = (-0.15, 0.15),
        max_goals: int = MAX_GOALS,
    ):
        self.xi = xi
        self.l2_reg = l2_reg
        self.rho_bounds = rho_bounds
        self.max_goals = max_goals

        # 训练后填充
        self.mu: float = 0.0
        self.home_adv: float = 0.0
        self.rho: float = 0.0
        self.attack: Dict[int, float] = {}
        self.defense: Dict[int, float] = {}
        self._team_index: Dict[int, int] = {}
        self._logfact = _logfact_upto(max_goals)

    # ------------------------------------------------------------------
    # 拟合
    # ------------------------------------------------------------------
    def fit(
        self,
        home_team: Sequence[int],
        away_team: Sequence[int],
        home_goals: Sequence[int],
        away_goals: Sequence[int],
        dates: Sequence,
        ref_date=None,
        xi: Optional[float] = None,
    ) -> "DixonColes":
        """MLE 拟合攻防强度。

        dates 为可做减法的 datetime 序列（用于时间衰减）；ref_date 为「预测时刻」，
        默认取 dates 的最大值。xi 可覆盖构造时的默认值。
        """
        if xi is not None:
            self.xi = xi

        teams = sorted(set(home_team) | set(away_team))
        self._team_index = {t: i for i, t in enumerate(teams)}
        n_teams = len(teams)

        hi = np.array([self._team_index[t] for t in home_team], dtype=np.int64)
        ai = np.array([self._team_index[t] for t in away_team], dtype=np.int64)
        x = np.asarray(home_goals, dtype=np.float64)
        y = np.asarray(away_goals, dtype=np.float64)

        w = self._time_weights(dates, ref_date)

        # 泛化 log-factorial：gammaln(x+1) 对整数进球与小数 xG 均成立，
        # 使同一套 MLE 既能拟合「实际进球」（计数）也能拟合「xG」（连续期望）。
        logfact_x = gammaln(x + 1.0)
        logfact_y = gammaln(y + 1.0)

        # 低分相关性修正只作用于 4 个格子
        low = (x == 0) & (y == 0)
        low01 = (x == 0) & (y == 1)
        low10 = (x == 1) & (y == 0)
        low11 = (x == 1) & (y == 1)

        # 初始值：mu=log(1.3), home_adv=0.25, rho=0, att/def=0
        theta0 = np.zeros(3 + 2 * n_teams)
        theta0[0] = np.log(1.3)
        theta0[1] = 0.25

        def unpack(theta):
            mu = theta[0]
            home_adv = theta[1]
            rho = theta[2]
            att = theta[3:3 + n_teams]
            defense = theta[3 + n_teams:3 + 2 * n_teams]
            return mu, home_adv, rho, att, defense

        def objective(theta):
            mu, home_adv, rho, att, defense = unpack(theta)
            lh = np.exp(mu + att[hi] + defense[ai] + home_adv)
            la = np.exp(mu + att[ai] + defense[hi])
            tau = self._tau_array(x, y, lh, la, rho)
            log_tau = np.log(np.clip(tau, 1e-8, None))

            log_lik = (
                log_tau
                + x * np.log(np.clip(lh, 1e-8, None)) - lh - logfact_x
                + y * np.log(np.clip(la, 1e-8, None)) - la - logfact_y
            )
            neg_ll = -np.sum(w * log_lik)
            penalty = self.l2_reg * (np.sum(att ** 2) + np.sum(defense ** 2))
            return neg_ll + penalty

        def gradient(theta):
            mu, home_adv, rho, att, defense = unpack(theta)
            lh = np.exp(mu + att[hi] + defense[ai] + home_adv)
            la = np.exp(mu + att[ai] + defense[hi])
            tau, d_lh, d_la, d_rho = self._tau_array_grad(x, y, lh, la, rho)

            # 泊松残差 + τ 修正项
            r_h = w * ((x - lh) + lh * d_lh / np.clip(tau, 1e-8, None))
            r_a = w * ((y - la) + la * d_la / np.clip(tau, 1e-8, None))

            g = np.zeros_like(theta)
            g[0] = -np.sum(r_h + r_a)                     # mu
            g[1] = -np.sum(r_h)                           # home_adv
            g[2] = -np.sum(w * d_rho / np.clip(tau, 1e-8, None))  # rho

            g_att = np.zeros(n_teams)
            g_def = np.zeros(n_teams)
            np.add.at(g_att, hi, -r_h)
            np.add.at(g_att, ai, -r_a)
            np.add.at(g_def, ai, -r_h)
            np.add.at(g_def, hi, -r_a)
            g[3:3 + n_teams] = g_att + 2 * self.l2_reg * att
            g[3 + n_teams:3 + 2 * n_teams] = g_def + 2 * self.l2_reg * defense
            return g

        bounds = [(None, None), (None, None), self.rho_bounds] + \
                 [(None, None)] * (2 * n_teams)

        res = minimize(
            objective, theta0, jac=gradient, method="L-BFGS-B",
            bounds=bounds, options={"maxiter": 2000, "ftol": 1e-9},
        )
        if not res.success:
            # 数值优化偶发失败时回退无梯度 BFGS（较慢但更稳）
            res = minimize(objective, theta0, method="L-BFGS-B",
                           bounds=bounds, options={"maxiter": 2000})

        mu, home_adv, rho, att, defense = unpack(res.x)
        self.mu = float(mu)
        self.home_adv = float(home_adv)
        self.rho = float(rho)
        for t, i in self._team_index.items():
            self.attack[t] = float(att[i])
            self.defense[t] = float(defense[i])
        return self

    # ------------------------------------------------------------------
    # 预测
    # ------------------------------------------------------------------
    def expected_goals(self, home_team: int, away_team: int) -> Tuple[float, float]:
        """返回 (λ_home, λ_away)。未见过的球队视为联赛均值（强度 0）。"""
        att_h = self.attack.get(home_team, 0.0)
        att_a = self.attack.get(away_team, 0.0)
        def_h = self.defense.get(home_team, 0.0)
        def_a = self.defense.get(away_team, 0.0)
        lh = float(np.exp(self.mu + att_h + def_a + self.home_adv))
        la = float(np.exp(self.mu + att_a + def_h))
        return lh, la

    def predict(self, home_team: int, away_team: int) -> dict:
        """预测比分矩阵 + 总进球分布 + 大小球概率。"""
        lh, la = self.expected_goals(home_team, away_team)
        K = self.max_goals + 1
        xs = np.arange(K)
        ys = np.arange(K)

        # 独立泊松网格
        p_x = np.exp(xs * np.log(np.clip(lh, 1e-8, None)) - lh - self._logfact[:K])
        p_y = np.exp(ys * np.log(np.clip(la, 1e-8, None)) - la - self._logfact[:K])
        P = np.outer(p_x, p_y)

        # Dixon-Coles τ 修正
        P = P * self._tau_grid(lh, la, self.rho, K)

        # 归一化（网格截断引入的小误差）
        P = P / np.sum(P)

        # 总进球分布（卷积）
        total = np.zeros(2 * K - 1)
        for k in range(2 * K - 1):
            total[k] = np.sum([P[i, k - i] for i in range(max(0, k - K + 1), min(k + 1, K))])
        total = total / np.sum(total)

        over_2_5 = float(np.sum(total[3:]))
        return {
            "home_expected": float(lh),
            "away_expected": float(la),
            "expected_total": float(lh + la),
            "score_matrix": P,
            "total_dist": {int(k): float(p) for k, p in enumerate(total)},
            "over_2_5": over_2_5,
            "under_2_5": float(1.0 - over_2_5),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _time_weights(self, dates, ref_date) -> np.ndarray:
        if ref_date is None:
            ref_date = max(dates)
        days = np.array([(ref_date - d).total_seconds() / 86400.0 for d in dates])
        days = np.clip(days, 0.0, None)
        return np.exp(-self.xi * days)

    @staticmethod
    def _tau_array(x, y, lh, la, rho):
        """仅对 4 个低分格子返回 τ，其余为 1。"""
        tau = np.ones_like(lh)
        m00 = (x == 0) & (y == 0)
        m01 = (x == 0) & (y == 1)
        m10 = (x == 1) & (y == 0)
        m11 = (x == 1) & (y == 1)
        tau[m00] = 1 - lh[m00] * la[m00] * rho
        tau[m01] = 1 + lh[m01] * rho
        tau[m10] = 1 + la[m10] * rho
        tau[m11] = 1 - rho
        return tau

    @staticmethod
    def _tau_array_grad(x, y, lh, la, rho):
        """返回 (tau, d_tau/d_lh, d_tau/d_la, d_tau/d_rho)。"""
        tau = np.ones_like(lh)
        d_lh = np.zeros_like(lh)
        d_la = np.zeros_like(la)
        d_rho = np.zeros_like(lh)

        m00 = (x == 0) & (y == 0)
        m01 = (x == 0) & (y == 1)
        m10 = (x == 1) & (y == 0)
        m11 = (x == 1) & (y == 1)

        tau[m00] = 1 - lh[m00] * la[m00] * rho
        d_lh[m00] = -la[m00] * rho
        d_la[m00] = -lh[m00] * rho
        d_rho[m00] = -lh[m00] * la[m00]

        tau[m01] = 1 + lh[m01] * rho
        d_lh[m01] = rho
        d_rho[m01] = lh[m01]

        tau[m10] = 1 + la[m10] * rho
        d_la[m10] = rho
        d_rho[m10] = la[m10]

        tau[m11] = 1 - rho
        d_rho[m11] = -1.0
        return tau, d_lh, d_la, d_rho

    def _tau_grid(self, lh, la, rho, K):
        """预测用的 τ 网格（K×K）。"""
        T = np.ones((K, K))
        if rho != 0.0:
            T[0, 0] = 1 - lh * la * rho
            T[0, 1] = 1 + lh * rho
            T[1, 0] = 1 + la * rho
            T[1, 1] = 1 - rho
        return np.clip(T, 1e-8, None)
