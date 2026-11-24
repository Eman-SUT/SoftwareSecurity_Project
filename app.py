import os
import sqlite3
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash)

app = Flask(__name__)
app.secret_key = "dev-key-change-later"

SECURE_MODE = False
DB_PATH = os.path.join(os.path.dirname(__file__), "shop.db")


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY,
        name TEXT,
        description TEXT,
        price REAL,
        stock INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY,
        product_id INTEGER,
        username TEXT,
        body TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        email TEXT,
        password TEXT,
        role TEXT DEFAULT 'user',
        credit REAL DEFAULT 100)""")

    db.execute("""CREATE TABLE IF NOT EXISTS coupons(
        code TEXT PRIMARY KEY,
        percent INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS coupon_uses(
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        code TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        product_id INTEGER,
        qty INTEGER,
        paid REAL,
        coupon TEXT)""")
    if db.execute("SELECT COUNT(*) FROM coupons").fetchone()[0] == 0:
        db.execute("INSERT INTO coupons(code, percent) VALUES ('SAVE10', 10)")

    if db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        products = [
            ("Laptop", "14-inch laptop, 16GB RAM", 800, 5),
            ("Headphones", "Wireless noise-cancelling", 120, 20),
            ("Keyboard", "Mechanical keyboard", 60, 15),
            ("Mouse", "Ergonomic wireless mouse", 25, 30),
            ("Monitor", "27-inch 4K monitor", 300, 8),
            ("USB Cable", "Braided USB-C cable", 8, 100),
        ]
        db.executemany(
            "INSERT INTO products(name, description, price, stock) VALUES (?, ?, ?, ?)",
            products)

    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        users = [
            ("admin", "admin@shop.test", "Admin@123", "admin", 1000),
            ("user", "user@shop.test", "User@123", "user", 100),
        ]
        db.executemany(
            "INSERT INTO users(username, email, password, role, credit) VALUES (?, ?, ?, ?, ?)",
            users)
    db.commit()
    db.close()


@app.route("/")
def index():
    db = get_db()
    products = db.execute("SELECT * FROM products").fetchall()
    db.close()
    return render_template("index.html", products=products)


@app.route("/product/<int:pid>")
def product(pid):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    reviews = db.execute(
        "SELECT * FROM reviews WHERE product_id=? ORDER BY id DESC",
        (pid,)).fetchall()
    db.close()
    return render_template("product.html", p=p, reviews=reviews)


@app.route("/product/<int:pid>/review", methods=["POST"])
def add_review(pid):
    body = request.form.get("body", "")
    username = session.get("username", "guest")
    db = get_db()
    db.execute(
        "INSERT INTO reviews(product_id, username, body) VALUES (?, ?, ?)",
        (pid, username, body))
    db.commit()
    db.close()
    return redirect(url_for("product", pid=pid))


@app.route("/checkout/<int:pid>", methods=["GET", "POST"])
def checkout(pid):
    if not session.get("uid"):
        flash("Please log in first.", "error")
        return redirect(url_for("login"))
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    if p is None:
        db.close()
        flash("Product not found.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        codes = [c.strip().upper() for c in request.form.getlist("coupon") if c.strip()]

        if not SECURE_MODE:
            price = float(request.form.get("price", p["price"]))
            qty = int(request.form.get("qty", 1))
            discount = 0
            for c in codes:
                row = db.execute("SELECT * FROM coupons WHERE code=?", (c,)).fetchone()
                if row:
                    discount += row["percent"]
            total = price * qty * (100 - discount) / 100
            new_credit = user["credit"] - total
            if new_credit < 0:
                db.close()
                flash("Not enough credit.", "error")
                return redirect(url_for("checkout", pid=pid))
            used_code = ",".join(codes) if codes else None
        else:
            try:
                qty = int(request.form.get("qty", 1))
            except ValueError:
                qty = 0
            if qty < 1 or qty > 10 or qty > p["stock"]:
                db.close()
                flash("Invalid quantity.", "error")
                return redirect(url_for("checkout", pid=pid))
            if len(codes) > 1:
                db.close()
                flash("Only one coupon per order.", "error")
                return redirect(url_for("checkout", pid=pid))
            price = p["price"]
            total = price * qty
            used_code = None
            if codes:
                code = codes[0]
                coupon = db.execute("SELECT * FROM coupons WHERE code=?", (code,)).fetchone()
                if coupon is None:
                    db.close()
                    flash("Invalid coupon.", "error")
                    return redirect(url_for("checkout", pid=pid))
                used = db.execute(
                    "SELECT 1 FROM coupon_uses WHERE user_id=? AND code=?",
                    (user["id"], code)).fetchone()
                if used:
                    db.close()
                    flash("You already used this coupon.", "error")
                    return redirect(url_for("checkout", pid=pid))
                total = total * (100 - coupon["percent"]) / 100
                used_code = code
                db.execute("INSERT INTO coupon_uses(user_id, code) VALUES (?, ?)", (user["id"], code))
            if total > user["credit"]:
                db.rollback()
                db.close()
                flash("Not enough credit.", "error")
                return redirect(url_for("checkout", pid=pid))
            new_credit = user["credit"] - total

        db.execute("UPDATE users SET credit=? WHERE id=?", (new_credit, user["id"]))
        db.execute("UPDATE products SET stock = stock - ? WHERE id=?", (qty, pid))
        db.execute(
            "INSERT INTO orders(user_id, product_id, qty, paid, coupon) VALUES (?, ?, ?, ?, ?)",
            (user["id"], pid, qty, total, used_code))
        db.commit()
        db.close()
        flash("Order placed! You paid $%.2f. New credit: $%.2f" % (total, new_credit), "ok")
        return redirect(url_for("checkout", pid=pid))

    db.close()
    return render_template("checkout.html", p=p, user=user, secure=SECURE_MODE)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "")
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users(username, email, password) VALUES (?, ?, ?)",
                (username, email, password))
            db.commit()
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")
            return render_template("register.html")
        finally:
            db.close()
        flash("Account created. Please log in.", "ok")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        db = get_db()
        row = db.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (username, password)
        ).fetchone()
        db.close()
        if row:
            session["uid"] = row["id"]
            session["username"] = row["username"]
            flash("Welcome, " + row["username"] + "!", "ok")
            return redirect(url_for("index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "ok")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=False, port=5000)