import pandas as pd

def get_shift_demand_data(file_path):
    """
    讀取需求表，並回傳方便查詢的字典格式
    """
    df = pd.read_csv(file_path)
    df['IfWeekend'] = df['IfWeekend'].fillna('N')

    # 準備兩個字典，讓 OR-Tools 之後可以透過天數 d (0~29) 來查詢
    is_weekend_dict = {}  # 紀錄某天是否為週末
    daily_demand_dict = {} # 紀錄某天各班別的需求人數

    # iterrows() 會一列一列讀取，index 剛好是 0 到 29，對應天數 d
    for index, row in df.iterrows():
        d = index 
        
        # 1. 判斷是否為週末 (如果是 'Y' 就存 True，否則存 False)
        is_weekend_dict[d] = True if row['IfWeekend'] == 'Y' else False
        
        # 2. 儲存當天早(D)、午(E)、晚(N)的需求人數
        daily_demand_dict[d] = {
            'D': int(row['Day']),
            'E': int(row['Afternoon']),
            'N': int(row['Night'])
        }

    # 把這兩個字典回傳出去給主程式用
    return is_weekend_dict, daily_demand_dict

def get_engineer_list(file_path):
    """
    讀取工程師名單與預排班表，並轉換為 OR-Tools 易於使用的格式。
    """
    df = pd.read_csv(file_path)
    
    # 準備要回傳的三個資料容器
    engineers_list = []       # 儲存工程師名字，例如: ['engineer_1', 'engineer_2', ...]
    default_groups = {}       # 儲存預設班別，例如: {0: 'D', 1: 'D', ...}
    pre_assigned_shifts = {}  # 儲存預排班表，例如: {(0, 0): 'E', (0, 1): 'O'}

    # df.iterrows() 讓我們一個一個工程師(一橫排)往下讀
    for e_index, row in df.iterrows():
        
        # 1. 取得工程師名字與預設群組 (使用 iloc 避免欄位名稱編碼問題)
        emp_name = row.iloc[0]  # 第 0 欄：人員名稱 (engineer_1)
        group = row.iloc[1]     # 第 1 欄：班別群組 (D, E, N)
        
        engineers_list.append(emp_name)
        default_groups[e_index] = group

        # 2. 讀取 Date_1 到 Date_30 的排班狀況
        # 迴圈跑 30 次，d_index 剛好是 0~29
        for d_index in range(30):
            # 組合出欄位名稱，例如 'Date_1', 'Date_2'
            date_col_name = f'Date_{d_index + 1}'
            
            # 把那一格的值拿出來
            shift_val = row[date_col_name]

            # 判斷這格「是不是空白的」？
            # pd.notna() 會幫我們過濾掉 Pandas 讀出來的 NaN (空白)
            if pd.notna(shift_val):
                shift_val = str(shift_val).strip() # 清除可能的隱藏空白
                
                # 如果填的是 D, E, N, O 其中一個，就把它記錄到預排字典裡
                if shift_val in ['D', 'E', 'N', 'O']:
                    # 字典的 Key 是一個 Tuple (員工編號, 天數編號)
                    pre_assigned_shifts[(e_index, d_index)] = shift_val

    return engineers_list, default_groups, pre_assigned_shifts
    
# --- 測試讀取 ---
if __name__ == "__main__":
    is_weekend, daily_demand = get_shift_demand_data('Shift_Demand.csv')
    
    # 測試查詢：印出第 0 天 (Date_1) 是不是週末？早班要幾人？
    print(f"第 0 天是週末嗎？ {is_weekend[0]}")
    print(f"第 0 天早班需求： {daily_demand[0]['D']} 人")