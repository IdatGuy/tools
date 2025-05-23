import pandas as pd
import sqlite3
import os

db_folder_name = 'db'
db_file_name = 'sales_data.db'
excel_folder_name = 'excel'
excel_file_name = 'store_daily_activity_report.xlsx'
db_file_path = os.path.join(db_folder_name, db_file_name)
excel_file_path = os.path.join(excel_folder_name, excel_file_name)

table_name = "SalesData"

sample_data = [
    (2025, 1, 1, "Jantzen Beach", 629, 1000.50, 200.75, 5),
    (2025, 1, 2, "Jantzen Beach", 629, 1500.00, 300.00, 10),
    (2025, 1, 3, "Jantzen Beach", 629, 2000.00, 400.00, 15),
]

# --- 1. Ensure the database folder exists ---
os.makedirs(db_folder_name, exist_ok=True) # Creates 'db' folder if it doesn't exist
print(f"Ensured '{db_folder_name}' directory exists.")
os.makedirs(excel_folder_name, exist_ok=True) # Creates 'excel' folder if it doesn't exist
print(f"Ensured '{excel_folder_name}' directory exists.")

# --- 2. Read Excel Data ---
try:
    # Assuming your data is on the first sheet, or specify sheet_name='Sheet1'
    df = pd.read_excel(excel_file_path)
    print("Excel data loaded successfully.")
    print(df.head()) # Display first few rows to verify
except FileNotFoundError:
    print(f"Error: Excel file not found at {excel_file_path}")
    exit()
except Exception as e:
    print(f"Error reading Excel file: {e}")
    exit()

# --- 3. Connect to SQLite Database ---
try:
    conn = sqlite3.connect(db_file_path)
    cursor = conn.cursor()
    print(f"Connected to SQLite database: {db_file_path}")
except Exception as e:
    print(f"Error connecting to database: {e}")
    exit()

create_table_sql = f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    StoreName TEXT NOT NULL,
    StoreID INTEGER NOT NULL,
    date DATE NOT NULL,
    TotalSales REAL,
    AccessorySales REAL,
    HomeConnectedSales INTEGER,
    HomePlusSales INTEGER
    Cleanings INTEGER,
    Repairs INTEGER,
);
"""
try:
    cursor.execute(create_table_sql)
    print(f"Table '{table_name}' checked/created successfully.")
    conn.commit() # Commit the table creation
except Exception as e:
    print(f"Error creating table: {e}")
    conn.close()
    exit()

# --- 4. Transfer Data to SQL Database ---
try:
    # 'append' adds new rows, 'replace' drops table and recreates, 'fail' raises error if table exists
    df.to_sql(table_name, conn, if_exists='append', index=False)
    print("Data transferred successfully to SQL database.")
except Exception as e:
    print(f"Error transferring data: {e}")
finally:
    # --- 5. Close Connection ---
    if conn:
        conn.close()
        print("Database connection closed.")

# --- 5. Verify Data by Querying the Database ---
print("\n--- Verifying data in the database ---")
try:
    cursor.execute(f"SELECT * FROM {table_name};")
    rows = cursor.fetchall() # Fetches all rows from the result of the query

    if not rows:
        print("No data found in the table.")
    else:
        for row in rows:
            print(row) # Each row is a tuple of values
except Exception as e:
    print(f"Error querying data: {e}")
finally:
    # --- 6. Close Connection ---
    if conn:
        conn.close()
        print("\nDatabase connection closed.")