from file_reader import get_shift_demand_data, get_engineer_list
from ortools.sat.python import cp_model # import Google or-tools 
import pandas as pd
model = cp_model.CpModel()

# 讀取 Shift_Demand.csv
is_weekend, daily_demand = get_shift_demand_data('Shift_Demand.csv')
# 讀取 Engineer_List.csv (取得預先排定的班表)
engineers_list, default_groups, pre_assigned_shifts = get_engineer_list('Engineer_List.csv')

# 建立決策變數矩陣 e = employee, d = days, s = shift
works = {}
for e in range(15):
    for d in range(30):
        for s in ['O', 'D', 'E', 'N']:
            works[(e, d, s)] = model.NewBoolVar(f'work_e{e}_d{d}_s{s}')

# 1. 唯一性：每人每天只能被分配到一個狀態 (D, E, N, O 其中一個為 1，其餘為 0)
for e in range(15):
    for d in range(30):
        model.AddExactlyOne(works[(e, d, s)] for s in ['O', 'D', 'E', 'N'])

# 2. 滿足人力需求：每天的各班別總人數，必須等於 Shift_Demand.csv 的規定
for d in range(30):
    demand_D = daily_demand[d]['D']        # 早班需求
    demand_E = daily_demand[d]['E']        # 午班需求
    demand_N = daily_demand[d]['N']        # 晚班需求
    model.Add(sum(works[(e, d, 'D')] for e in range(15)) == demand_D) # 早班 constraint
    model.Add(sum(works[(e, d, 'E')] for e in range(15)) == demand_E) # 午班 constraint
    model.Add(sum(works[(e, d, 'N')] for e in range(15)) == demand_N) # 晚班 constraint

# 3. 預先排定不被改變：讀取 Engineer_List.csv，如果某人那天已經排定
# 例如員工 0 第 0 天預設排 'E'
for (e, d), s in pre_assigned_shifts.items():
    # 強制該員工 (e) 在該天 (d) 的該班別/休假 (s) 的布林變數為 1 (True)
    model.Add(works[(e, d, s)] == 1)

if __name__ == "__main__":
    # 4. 建立求解器並開始求解
    solver = cp_model.CpSolver()
    
    # 這裡可以設定運算時間上限 (例如 60 秒)，避免模型太複雜卡住
    solver.parameters.max_time_in_seconds = 60.0 
    
    print("開始求解...")
    status = solver.Solve(model)

    # 5. 判斷求解狀態並輸出
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        print('找到可行解！準備匯出資料...')
        
        # 準備建立輸出的 DataFrame
        # 表頭：人員, 班別群組, Date_1, Date_2, ..., Date_30
        columns = ['人員', '班別群組'] + [f'Date_{d+1}' for d in range(30)]
        output_data = []

        # 逐一檢視每位工程師 (e) 在每一天 (d) 的排班狀態
        for e in range(15):
            emp_name = engineers_list[e]
            group = default_groups[e]
            row_data = [emp_name, group]
            
            for d in range(30):
                # 檢查四種班別，哪一個的布林變數被求解器設為 1 (True)
                for s in ['O', 'D', 'E', 'N']:
                    if solver.Value(works[(e, d, s)]) == 1:
                        # 將排程結果填入 (D:日班、E:午班、N:晚班、O:休假)
                        row_data.append(s)
                        break # 找到就換下一天
            
            output_data.append(row_data)

        # 將結果轉換為 Pandas DataFrame 並存成 CSV
        # 命名為 Scheduling_Output.csv
        output_df = pd.DataFrame(output_data, columns=columns)
        output_df.to_csv('Scheduling_Output.csv', index=False, encoding='utf-8-sig')
        
        print('排班結果已成功匯出至 Scheduling_Output.csv')
    else:
        print('找不到符合硬限制的班表，請檢查資料是否有衝突。')
