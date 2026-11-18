import pandas as pd
from pymongo import MongoClient

# Connect to MongoDB server
client = MongoClient("mongodb://localhost:27017")

# Access database
db = client.shop

# Access collection
orders = db.orders

print("Connected to MongoDB!")
print("Database:", db.name)
print("Collection:", orders.name)

# Insert one order
result = orders.insert_one({
    "_id": 1004,
    "customer": {
        "name": "Hung",
        "city": "Hue"
    },
    "status": "paid",
    "items": []
})

print("Inserted ID:", result.inserted_id)

# Find all paid orders
for doc in orders.find(
    {"status": "paid"},
    {"customer.name": 1, "status": 1}
):
    print(doc)

pipeline = [
    {"$unwind": "$items"},
    {"$group": {"_id": "$items.sku", "units": {"$sum": "$items.qty"}}},
]
df = pd.DataFrame(orders.aggregate(pipeline))      # straight into pandas
print(df)