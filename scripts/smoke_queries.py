import sys
from pathlib import Path
# Make project root importable when running scripts directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from text2sql.db import execute_select

QUERIES = [
    "SELECT * FROM customers LIMIT 5",
    "SELECT * FROM orders LIMIT 5",
    """SELECT c.company_name, COUNT(*) AS n_orders
       FROM orders o JOIN customers c ON o.customer_id = c.customer_id
       GROUP BY c.company_name
       ORDER BY n_orders DESC LIMIT 5""",
    """SELECT c.category_name,
              SUM(od.unit_price * od.quantity * (1 - od.discount)) AS revenue
       FROM order_details od
       JOIN products p ON p.product_id = od.product_id
       JOIN categories c ON c.category_id = p.category_id
       GROUP BY c.category_name
       ORDER BY revenue DESC LIMIT 5""",
]

if __name__ == "__main__":
    for q in QUERIES:
        print("\nSQL:", q)
        df = execute_select(q, max_rows=10)
        print(df.head(10))
