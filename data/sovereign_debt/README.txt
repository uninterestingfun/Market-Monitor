把各国财政部/统计局导出的债务数据按 CSV 格式放进来，文件名用 ISO3 代码，例如 USA.csv、JPN.csv。
程序会优先使用这里的数字，覆盖世界银行与内置接口；界面上会标记「本」。

格式：date,debt_pct,debt_amount,source
  debt_pct 与 debt_amount 至少填一个；只填金额时自动除以最新 GDP。

示例（日本）：
date,debt_pct,debt_amount,source
2026-06-30,248.7,,日本财务省
