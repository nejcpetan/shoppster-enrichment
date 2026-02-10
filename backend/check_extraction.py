import sqlite3
import json

conn = sqlite3.connect('products.db')
conn.row_factory = sqlite3.Row
row = conn.execute('SELECT extraction_result FROM products WHERE id = 4').fetchone()
data = json.loads(row['extraction_result'])

print('Extracted fields with values:')
for field, value in data.items():
    if isinstance(value, dict) and value.get('value') is not None:
        print(f'  {field}: {value.get("value")} {value.get("unit", "")}')
