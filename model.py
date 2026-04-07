from file_reader import get_shift_demand_data, get_engineer_list
from ortools.sat.python import cp_model # import Google or-tools 
import pandas as pd

model = cp_model.CpModel()

# 讀取 Shift_Demand.csv
is_weekend, daily_demand = get_shift_demand_data('Shift_Demand.csv')
# 讀取 Engineer_List.csv (取得預先排定的班表)
engineers_list, default_groups, pre_assigned_shifts = get_engineer_list('Engineer_List.csv')

NUM_ENGINEERS = 15
NUM_DAYS = 30
SHIFTS = ['O', 'D', 'E', 'N']

# ============================================================
# 建立決策變數矩陣 e = employee, d = days, s = shift
# ============================================================
works = {}
for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS):
        for s in SHIFTS:
            works[(e, d, s)] = model.NewBoolVar(f'work_e{e}_d{d}_s{s}')

# ============================================================
# 硬限制 1: 唯一性 — 每人每天只能被分配到一個狀態
# ============================================================
for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS):
        model.AddExactlyOne(works[(e, d, s)] for s in SHIFTS)

# ============================================================
# 硬限制 2: 滿足人力需求 — 每天各班別總人數必須等於需求
# ============================================================
for d in range(NUM_DAYS):
    demand_D = daily_demand[d]['D']
    demand_E = daily_demand[d]['E']
    demand_N = daily_demand[d]['N']
    model.Add(sum(works[(e, d, 'D')] for e in range(NUM_ENGINEERS)) == demand_D)
    model.Add(sum(works[(e, d, 'E')] for e in range(NUM_ENGINEERS)) == demand_E)
    model.Add(sum(works[(e, d, 'N')] for e in range(NUM_ENGINEERS)) == demand_N)

# ============================================================
# 硬限制 3: 預排班不可更改
# ============================================================
for (e, d), s in pre_assigned_shifts.items():
    model.Add(works[(e, d, s)] == 1)

# ============================================================
# 輔助變數: is_working[e, d] = 1 表示該員工當天有上班 (非休假)
# is_working[e, d] = 1 - works[(e, d, 'O')]
# 也就是說：不是休假 = 正在上班
# ============================================================
is_working = {}
for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS):
        is_working[(e, d)] = model.NewBoolVar(f'is_working_e{e}_d{d}')
        # is_working = 1 當且僅當 works[O] = 0
        # 等價於 is_working + works[O] = 1
        model.Add(is_working[(e, d)] + works[(e, d, 'O')] == 1)

# ============================================================
# 軟限制（懲罰）: 連續上班6天 (權重 = 1.0)
# ============================================================
# 邏輯說明：
#   - 對每位員工，用滑動窗口檢查每連續 6 天是否都在上班
#   - 窗口範圍: d, d+1, d+2, d+3, d+4, d+5 (共 6 天)
#   - 如果這 6 天全部上班 → 懲罰 1 次
#
# 為什麼這樣計算是正確的？
#   - 假設某人連續上班恰好 6 天 (例如 Day0~Day5)
#     → 只有 1 個窗口 (起點=Day0) 能讓 6 天全部為上班
#     → 懲罰 = 1 次 ✓
#   - 假設某人連續上班 7 天 (Day0~Day6)
#     → 2 個窗口 (起點=Day0, Day1) 都滿足 6 天全上班
#     → 懲罰 = 2 次 ✓
#   - 連續上班 k 天 (k≥6) → 懲罰 = k-5 次 ✓
#
# 實作方式：
#   使用 Reification (具體化約束)
#   penalty_consec6[e, d] = 1  ⟺  is_working[e, d] ~ is_working[e, d+5] 全部為 1
# ============================================================

penalty_consec6 = {}  # 儲存每個窗口的懲罰變數
WEIGHT_CONSEC6 = 1.0  # 權重 (之後用整數表示，乘以 10 避免浮點數)

for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS - 5):  # d 從 0 到 24 (最後一個窗口是 Day24~Day29)
        # 建立懲罰布林變數
        penalty_var = model.NewBoolVar(f'penalty_consec6_e{e}_d{d}')
        penalty_consec6[(e, d)] = penalty_var
        
        # 取出這 6 天的上班變數
        window = [is_working[(e, d + i)] for i in range(6)]
        
        # 約束: penalty_var = 1 ⟺ 這 6 天全部上班
        # 
        # 方法: 使用 AddBoolAnd + OnlyEnforceIf (具體化約束)
        #   (a) 如果 penalty_var = 1 → 這 6 天必須全上班
        #   (b) 如果這 6 天全上班 → penalty_var 必須 = 1
        
        # (a) penalty_var = 1 → 每天都上班
        for w in window:
            model.Add(w == 1).OnlyEnforceIf(penalty_var)
        
        # (b) 如果 6 天全上班 → penalty_var = 1
        #     等價於: 如果 penalty_var = 0 → 至少有一天不上班
        #     使用: sum(window) <= 5 when penalty_var = 0
        model.Add(sum(window) <= 5).OnlyEnforceIf(penalty_var.Not())

# ============================================================
# 軟限制（懲罰）: 班別銜接違規 (權重 = 1.0)
# ============================================================
# 題目規定以下四種隔天銜接都違法，每發生一次懲罰 1 次：
#   (1) 夜班 → 早班 (N→D)
#   (2) 夜班 → 午班 (N→E)
#   (3) 午班 → 早班 (E→D)
#   (4) 早班 → 夜班 (D→N)
#   (5) 午班 → 夜班 (E→N)
#
# 實作方式：
#   如果兩天同時成立 (例如 Day d 是 N 且 Day d+1 是 D)，
#   那 works[(e,d,'N')] + works[(e,d+1,'D')] = 2，
#   我們用一個懲罰變數捕捉這個情況。
# ============================================================

# 定義所有違規的班別銜接對 (前一天班別, 隔天班別)
illegal_transitions = [
    ('N', 'D'),  # 夜班 → 早班
    ('N', 'E'),  # 夜班 → 午班
    ('E', 'D'),  # 午班 → 早班
    ('D', 'N'),  # 早班 → 夜班
    ('E', 'N'),  # 午班 → 夜班
]

penalty_transition = {}

for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS - 1):  # d 從 0 到 28 (檢查 d 和 d+1)
        for (s_prev, s_next) in illegal_transitions:
            penalty_var = model.NewBoolVar(
                f'penalty_trans_e{e}_d{d}_{s_prev}{s_next}'
            )
            penalty_transition[(e, d, s_prev, s_next)] = penalty_var
            
            # penalty_var = 1 ⟺ works[(e,d,s_prev)] = 1 且 works[(e,d+1,s_next)] = 1
            
            # (a) penalty_var = 1 → 兩個班別都必須成立
            model.Add(works[(e, d, s_prev)] == 1).OnlyEnforceIf(penalty_var)
            model.Add(works[(e, d + 1, s_next)] == 1).OnlyEnforceIf(penalty_var)
            
            # (b) 兩個班別都成立 → penalty_var = 1
            #     等價於: penalty_var = 0 → 至少有一個不成立
            model.Add(
                works[(e, d, s_prev)] + works[(e, d + 1, s_next)] <= 1
            ).OnlyEnforceIf(penalty_var.Not())

# ============================================================
# 軟限制（懲罰）: 違反預設班別 (權重 = 0.2)
# ============================================================
# 每位員工有一個預設班別群組 (D, E, 或 N)。
# 如果某天被排到「非休假」且「不是預設班別」的班，就算違規 1 次。
# 例如：預設群組為 D 的員工，被排到 E 或 N 就各算 1 次。
#       被排到 O (休假) 不算違規。
#
# 實作方式：
#   penalty_group[e, d] = 1 ⟺ 上班 且 班別 ≠ 預設群組
#   也就是: is_working[e,d] = 1 且 works[(e,d,default_group)] = 0
# ============================================================

penalty_group = {}

for e in range(NUM_ENGINEERS):
    group = default_groups[e]  # 該員工的預設班別 (D, E, 或 N)
    for d in range(NUM_DAYS):
        penalty_var = model.NewBoolVar(f'penalty_group_e{e}_d{d}')
        penalty_group[(e, d)] = penalty_var
        
        # penalty_var = 1 ⟺ 上班 (非休假) 且 不是排預設班別
        # 等價於: penalty_var = 1 ⟺ works[O]=0 且 works[group]=0
        # 也就是: penalty_var = 1 ⟺ is_working=1 且 works[group]=0
        
        # (a) penalty_var = 1 → 必須上班 且 不是預設班別
        model.Add(is_working[(e, d)] == 1).OnlyEnforceIf(penalty_var)
        model.Add(works[(e, d, group)] == 0).OnlyEnforceIf(penalty_var)
        
        # (b) 上班 且 不是預設班別 → penalty_var = 1
        #     等價於: penalty_var = 0 → 休假 或 排預設班別
        #     即: works[O] + works[group] >= 1 when penalty_var = 0
        model.Add(
            works[(e, d, 'O')] + works[(e, d, group)] >= 1
        ).OnlyEnforceIf(penalty_var.Not())

# ============================================================
# 軟限制（懲罰）: 每人每月連續休假次數 < 2 次 (權重 = 0.1)
# ============================================================
# 「連續休假」定義：連續 2 天以上的休假算 1 次連續休假。
#   - 隔日也算休假（即只要連續天都是 O 就算）
#   - 連續休假 3 天以上也只算 1 次
#   - 每人整月的連續休假次數如果是 0 或 1 次，就懲罰 1 次
#     （只有 0 次和 1 次會罰，因為題目說「小於2次」）
#
# 實作方式：
#   先偵測每段連續休假的「起點」：
#   consec_off_start[e,d] = 1 ⟺ Day d 休假 且 Day d-1 不休假（或 d=0 且休假）
#   連續休假次數 = 所有起點的總和，但我們只需要計算長度 ≥ 2 的連續休假
#
#   更精確：一段連續休假如果長度只有 1 天，那不算「連續休假」。
#   所以連續休假起點需要：Day d 休假 且 Day d+1 也休假 且 (d=0 或 Day d-1 不休假)
# ============================================================

# 偵測長度 ≥ 2 的連續休假段的「起點」
consec_off_start = {}
for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS - 1):  # 需要 d+1 存在
        var = model.NewBoolVar(f'consec_off_start_e{e}_d{d}')
        consec_off_start[(e, d)] = var
        
        if d == 0:
            # 第 0 天：只要 Day0 和 Day1 都休假，就是一個連續休假起點
            # var = 1 ⟺ works[O,d0]=1 且 works[O,d1]=1
            model.Add(works[(e, 0, 'O')] == 1).OnlyEnforceIf(var)
            model.Add(works[(e, 1, 'O')] == 1).OnlyEnforceIf(var)
            model.Add(
                works[(e, 0, 'O')] + works[(e, 1, 'O')] <= 1
            ).OnlyEnforceIf(var.Not())
        else:
            # d >= 1：Day d 休假 且 Day d+1 休假 且 Day d-1 不休假
            # var = 1 ⟺ works[O,d-1]=0 且 works[O,d]=1 且 works[O,d+1]=1
            model.Add(works[(e, d - 1, 'O')] == 0).OnlyEnforceIf(var)
            model.Add(works[(e, d, 'O')] == 1).OnlyEnforceIf(var)
            model.Add(works[(e, d + 1, 'O')] == 1).OnlyEnforceIf(var)
            # 反向：var = 0 → 以上三個條件至少有一個不成立
            # 即 (1 - works[O,d-1]) + works[O,d] + works[O,d+1] <= 2
            model.Add(
                (1 - works[(e, d - 1, 'O')]) + works[(e, d, 'O')] + works[(e, d + 1, 'O')] <= 2
            ).OnlyEnforceIf(var.Not())

# 每人的連續休假次數
consec_off_count = {}
penalty_consec_off = {}
for e in range(NUM_ENGINEERS):
    total = model.NewIntVar(0, NUM_DAYS, f'consec_off_count_e{e}')
    model.Add(total == sum(consec_off_start[(e, d)] for d in range(NUM_DAYS - 1)))
    consec_off_count[e] = total
    
    # 懲罰: 如果 total < 2 (即 0 或 1)，懲罰 1 次
    penalty_var = model.NewBoolVar(f'penalty_consec_off_e{e}')
    penalty_consec_off[e] = penalty_var
    
    # penalty_var = 1 ⟺ total < 2 ⟺ total <= 1
    model.Add(total <= 1).OnlyEnforceIf(penalty_var)
    model.Add(total >= 2).OnlyEnforceIf(penalty_var.Not())

# ============================================================
# 軟限制（懲罰）: 每人每月休假天數 < 9 天 (權重 = 0.1)
# ============================================================
# 如果某人整月休假天數不足 9 天，每少 1 天懲罰 1 次。
# 例如：休假 8 天 → 懲罰 1 次，休假 7 天 → 懲罰 2 次
#
# 實作方式：
#   off_count[e] = 該員工整月休假天數
#   shortfall[e] = max(0, 9 - off_count[e])  → 這就是懲罰次數
# ============================================================

off_count = {}
penalty_off_days = {}

for e in range(NUM_ENGINEERS):
    # 計算整月休假天數
    total_off = model.NewIntVar(0, NUM_DAYS, f'off_count_e{e}')
    model.Add(total_off == sum(works[(e, d, 'O')] for d in range(NUM_DAYS)))
    off_count[e] = total_off
    
    # shortfall = max(0, 9 - total_off)
    # 用 IntVar 表示: shortfall >= 0, shortfall >= 9 - total_off
    shortfall = model.NewIntVar(0, 9, f'penalty_off_days_e{e}')
    model.Add(shortfall >= 9 - total_off)
    model.Add(shortfall >= 0)
    # 為了讓 shortfall 精確等於 max(0, 9-total_off)，
    # 在 Minimize 目標下，求解器會自動把 shortfall 壓到最小值
    # 所以 shortfall 會自然等於 max(0, 9-total_off)
    penalty_off_days[e] = shortfall

# ============================================================
# 軟限制（懲罰）: 周末休假天數 < 4 天 (權重 = 0.1)
# ============================================================
# 若該人在所有周末日中，休假天數不足 4 天，每少 1 天懲罰 1 次。
# 周末日由 Shift_Demand.csv 的 IfWeekend='Y' 決定。
#
# 實作方式：
#   weekend_off[e] = 該員工在所有周末日的休假天數
#   shortfall = max(0, 4 - weekend_off[e])
# ============================================================

# 找出所有周末日
weekend_days = [d for d in range(NUM_DAYS) if is_weekend[d]]
num_weekends = len(weekend_days)

weekend_off_count = {}
penalty_weekend_off = {}

for e in range(NUM_ENGINEERS):
    total_wknd_off = model.NewIntVar(0, num_weekends, f'weekend_off_e{e}')
    model.Add(total_wknd_off == sum(works[(e, d, 'O')] for d in weekend_days))
    weekend_off_count[e] = total_wknd_off
    
    shortfall = model.NewIntVar(0, 4, f'penalty_weekend_off_e{e}')
    model.Add(shortfall >= 4 - total_wknd_off)
    model.Add(shortfall >= 0)
    penalty_weekend_off[e] = shortfall

# ============================================================
# 軟限制（懲罰）: 僅排休 1 日（非連續休假）(權重 = 0.1)
# ============================================================
# 如果某天休假，但前一天和後一天都上班，這就是「孤立休假」，懲罰 1 次。
# 注意：第一天 (Day 0) 和最後一天 (Day 29) 的單獨休假不計懲罰。
#
# 實作方式：
#   isolated_off[e,d] = 1 ⟺ Day d 休假 且 Day d-1 上班 且 Day d+1 上班
#   只對 d = 1 ~ 28 檢查（排除首尾）
# ============================================================

penalty_isolated_off = {}

for e in range(NUM_ENGINEERS):
    for d in range(1, NUM_DAYS - 1):  # d 從 1 到 28
        penalty_var = model.NewBoolVar(f'penalty_isolated_off_e{e}_d{d}')
        penalty_isolated_off[(e, d)] = penalty_var
        
        # penalty_var = 1 ⟺ Day d-1 上班 且 Day d 休假 且 Day d+1 上班
        # 即: is_working[d-1]=1 且 works[O,d]=1 且 is_working[d+1]=1
        
        # (a) penalty_var = 1 → 三個條件都成立
        model.Add(is_working[(e, d - 1)] == 1).OnlyEnforceIf(penalty_var)
        model.Add(works[(e, d, 'O')] == 1).OnlyEnforceIf(penalty_var)
        model.Add(is_working[(e, d + 1)] == 1).OnlyEnforceIf(penalty_var)
        
        # (b) 三個條件都成立 → penalty_var = 1
        #     等價於: penalty_var = 0 → 至少一個條件不成立
        #     is_working[d-1] + works[O,d] + is_working[d+1] <= 2 when penalty=0
        model.Add(
            is_working[(e, d - 1)] + works[(e, d, 'O')] + is_working[(e, d + 1)] <= 2
        ).OnlyEnforceIf(penalty_var.Not())

# ============================================================
# 目標函式: 最小化所有懲罰的加權總和
# ============================================================
# 注意：OR-Tools 的 Minimize 只接受整數
# 所有權重乘以 10 統一尺度: 1.0→10, 0.2→2, 0.1→1

objective_terms = []

# --- 違法性 ---
# 連續上班6天的懲罰 (權重 1.0 → 10)
for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS - 5):
        objective_terms.append(10 * penalty_consec6[(e, d)])

# 班別銜接違規的懲罰 (權重 1.0 → 10)
for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS - 1):
        for (s_prev, s_next) in illegal_transitions:
            objective_terms.append(10 * penalty_transition[(e, d, s_prev, s_next)])

# 違反預設班別的懲罰 (權重 0.2 → 2)
for e in range(NUM_ENGINEERS):
    for d in range(NUM_DAYS):
        objective_terms.append(2 * penalty_group[(e, d)])

# --- 公平性 ---
# 連續休假次數 < 2 (權重 0.1 → 1)
for e in range(NUM_ENGINEERS):
    objective_terms.append(1 * penalty_consec_off[e])

# 月休假天數 < 9 (權重 0.1 → 1, shortfall 本身就是懲罰次數)
for e in range(NUM_ENGINEERS):
    objective_terms.append(1 * penalty_off_days[e])

# 周末休假天數 < 4 (權重 0.1 → 1)
for e in range(NUM_ENGINEERS):
    objective_terms.append(1 * penalty_weekend_off[e])

# 僅排休1日 (權重 0.1 → 1)
for e in range(NUM_ENGINEERS):
    for d in range(1, NUM_DAYS - 1):
        objective_terms.append(1 * penalty_isolated_off[(e, d)])

model.Minimize(sum(objective_terms))

# ============================================================
# 求解與輸出
# ============================================================
if __name__ == "__main__":
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 25.0
    solver.parameters.log_search_progress = True

    print("開始求解...")
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        # 演算法資訊
        print(f'--- 演算法資訊 ---')
        print(f'  Run Time:  {solver.WallTime():.2f} 秒')
        print(f'  評估次數:  {solver.NumBranches()}')
        print(f'找到可行解！目標值(加權懲罰總和) = {solver.ObjectiveValue() / 10:.2f}')
        
        # --- 統計各項懲罰 ---
        consec6_count = sum(
            solver.Value(penalty_consec6[(e, d)])
            for e in range(NUM_ENGINEERS)
            for d in range(NUM_DAYS - 5)
        )
        transition_count = sum(
            solver.Value(penalty_transition[(e, d, s_prev, s_next)])
            for e in range(NUM_ENGINEERS)
            for d in range(NUM_DAYS - 1)
            for (s_prev, s_next) in illegal_transitions
        )
        group_count = sum(
            solver.Value(penalty_group[(e, d)])
            for e in range(NUM_ENGINEERS)
            for d in range(NUM_DAYS)
        )
        consec_off_violation = sum(
            solver.Value(penalty_consec_off[e])
            for e in range(NUM_ENGINEERS)
        )
        off_days_shortfall = sum(
            solver.Value(penalty_off_days[e])
            for e in range(NUM_ENGINEERS)
        )
        weekend_off_shortfall = sum(
            solver.Value(penalty_weekend_off[e])
            for e in range(NUM_ENGINEERS)
        )
        isolated_off_count = sum(
            solver.Value(penalty_isolated_off[(e, d)])
            for e in range(NUM_ENGINEERS)
            for d in range(1, NUM_DAYS - 1)
        )
        
        print(f'--- 違法性懲罰統計 ---')
        print(f'  連續上班6天:     {consec6_count} 次 (權重1.0, 懲罰值={consec6_count * 1.0:.1f})')
        print(f'  班別銜接違規:    {transition_count} 次 (權重1.0, 懲罰值={transition_count * 1.0:.1f})')
        print(f'  違反預設班別:    {group_count} 次 (權重0.2, 懲罰值={group_count * 0.2:.1f})')
        print(f'--- 公平性懲罰統計 ---')
        print(f'  月連續休假<2:    {consec_off_violation} 次 (權重0.1, 懲罰值={consec_off_violation * 0.1:.1f})')
        print(f'  月休假天數不足:  {off_days_shortfall} 次 (權重0.1, 懲罰值={off_days_shortfall * 0.1:.1f})')
        print(f'  周末休假不足:    {weekend_off_shortfall} 次 (權重0.1, 懲罰值={weekend_off_shortfall * 0.1:.1f})')
        print(f'  僅排休1日:      {isolated_off_count} 次 (權重0.1, 懲罰值={isolated_off_count * 0.1:.1f})')
        
        total_penalty = (consec6_count * 1.0 + transition_count * 1.0 + group_count * 0.2
                        + consec_off_violation * 0.1 + off_days_shortfall * 0.1
                        + weekend_off_shortfall * 0.1 + isolated_off_count * 0.1)
        print(f'--- 總懲罰值: {total_penalty:.2f} ---')

        # 準備建立輸出的 DataFrame
        columns = ['人員', '班別群組'] + [f'Date_{d+1}' for d in range(NUM_DAYS)]
        output_data = []

        for e in range(NUM_ENGINEERS):
            emp_name = engineers_list[e]
            group = default_groups[e]
            row_data = [emp_name, group]

            for d in range(NUM_DAYS):
                for s in SHIFTS:
                    if solver.Value(works[(e, d, s)]) == 1:
                        row_data.append(s)
                        break

            output_data.append(row_data)

        output_df = pd.DataFrame(output_data, columns=columns)
        output_df.to_csv('Scheduling_Output.csv', index=False, encoding='utf-8-sig')
        print('排班結果已成功匯出至 Scheduling_Output.csv')
    else:
        print('找不到符合硬限制的班表，請檢查資料是否有衝突。')