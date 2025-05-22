import pandas as pd
import sqlite3

table_name = "SalesData"

create_table_sql = f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    Year TEXT NOT NULL,
    Month TEXT NOT NULL,
    Day TEXT NOT NULL,
    StoreName TEXT NOT NULL,
    StoreID TEXT NOT NULL,
    TotalSales REAL,
    AccessorySales REAL,
    ConnectedHomeSales REAL
);
"""