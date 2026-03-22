from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueClient
from flask import Flask, render_template, request, redirect
from pymongo import MongoClient
from bson.objectid import ObjectId
import json
import os
import psycopg2

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ---------------- AZURE STORAGE ----------------

connection_string = os.environ.get("STORAGE_CONNECTION_STRING")

blob_service_client = BlobServiceClient.from_connection_string(connection_string)

container_name = "productimages"
container_client = blob_service_client.get_container_client(container_name)

queue_client = QueueClient.from_connection_string(
    connection_string,
    "storage-queue"
)


# ---------------- MONGODB ----------------

cart_collection = None

try:
    client = MongoClient("mongodb://10.0.1.4:27017/")
    db = client["shopping_db"]
    cart_collection = db["cart"]
    print("MongoDB connected")

except Exception as e:
    print("MongoDB connection failed:", e)


# ---------------- POSTGRESQL ----------------

pg_conn = None
pg_cursor = None

try:
    pg_conn = psycopg2.connect(
        host=os.environ.get("PG_HOST"),
        database=os.environ.get("PG_DB"),
        user=os.environ.get("PG_USER"),
        password=os.environ.get("PG_PASSWORD"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        sslmode="require"
    )

    pg_cursor = pg_conn.cursor()
    print("PostgreSQL connected")

except Exception as e:
    print("PostgreSQL connection failed:", e)


# ---------------- LOAD PRODUCTS ----------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(BASE_DIR, "products.json")

with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
    products = json.load(f)


# ---------------- HOME PAGE ----------------

@app.route("/")
def home():
    return render_template("index.html", products=products)


# ---------------- TEXT SEARCH ----------------

@app.route("/search")
def search():

    query = request.args.get("query", "").lower()

    if query:
        try:
            queue_client.send_message(f"Search query: {query}")
        except:
            pass

    filtered = [p for p in products if query in p["name"].lower()]

    return render_template("index.html", products=filtered)


# ---------------- IMAGE SEARCH ----------------

@app.route("/upload", methods=["POST"])
def upload():

    image = request.files.get("image")

    if image and image.filename != "":

        filename = image.filename.lower()

        blob_client = container_client.get_blob_client(filename)
        blob_client.upload_blob(image, overwrite=True)

        image_url = blob_client.url   # ✅ important

        keyword = filename.split(".")[0]
        results = [p for p in products if keyword in p["name"].lower()]

        if not results:
            results = products

        return render_template(
            "index.html",
            products=results,
            image_url=image_url   # ✅ pass this
        )

    return redirect("/")


# ---------------- ADD TO CART ----------------

@app.route("/add_to_cart/<int:pid>")
def add_to_cart(pid):

    if cart_collection is None:
        return "MongoDB connection failed"

    for p in products:

        if p["id"] == pid:

            item = p.copy()

            if "_id" in item:
                del item["_id"]

            cart_collection.insert_one(item)

            try:
                queue_client.send_message(f"Added to cart: {p['name']}")
            except:
                pass

            break

    return redirect("/cart")


# ---------------- CART PAGE ----------------

@app.route("/cart")
def cart_page():

    if cart_collection is None:
        return "MongoDB connection failed"

    cart_items = list(cart_collection.find())

    return render_template("cart.html", cart=cart_items)


# ---------------- REMOVE ITEM ----------------

@app.route("/remove/<id>")
def remove(id):

    if cart_collection is None:
        return "MongoDB connection failed"

    item = cart_collection.find_one({"_id": ObjectId(id)})

    if item:
        try:
            queue_client.send_message(f"Removed from cart: {item['name']}")
        except:
            pass

    cart_collection.delete_one({"_id": ObjectId(id)})

    return redirect("/cart")


# ---------------- PURCHASE SELECTED ----------------

@app.route("/purchase_selected", methods=["POST"])
def purchase_selected():

    if cart_collection is None:
        return "MongoDB connection failed"

    if pg_cursor is None:
        return "PostgreSQL connection failed"

    selected_ids = request.form.getlist("selected_items")

    purchased_items = []

    for sid in selected_ids:

        item = cart_collection.find_one({"_id": ObjectId(sid)})

        if item:

            purchased_items.append(item)

            try:
                queue_client.send_message(f"Purchase completed: {item['name']}")
            except:
                pass

            try:
                pg_cursor.execute(
                    "INSERT INTO purchases(product_id, name, price) VALUES (%s,%s,%s)",
                    (int(item["id"]), item["name"], int(item["price"]))
                )

                pg_conn.commit()

                print("Inserted:", item["name"])

            except Exception as e:
                print("PostgreSQL insert error:", e)

            cart_collection.delete_one({"_id": ObjectId(sid)})

    return render_template("purchase.html", items=purchased_items)


# ---------------- PURCHASE HISTORY ----------------

@app.route("/history")
def history():

    if pg_cursor is None:
        return "PostgreSQL connection failed"

    pg_cursor.execute("SELECT name, price FROM purchases")

    rows = pg_cursor.fetchall()

    items = []

    for r in rows:
        items.append({
            "name": r[0],
            "price": r[1]
        })

    return render_template("history.html", items=items)


# ---------------- RUN APP ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
