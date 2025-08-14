from flask import Flask, render_template, request, redirect, url_for, session, abort
import json, random, requests, html, os
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "supersecretkey"

QUIZ_FILE = "quizzes.json"
USERS_FILE = "users.json"

# ---------- Users helpers ----------
def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            users = json.load(f)
    except:
        users = {}

    # Auto-upgrade old string-only entries to dict format
    upgraded = False
    for uname, val in list(users.items()):
        if isinstance(val, str):  # old format was hashed string
            users[uname] = {"password": val, "role": "student"}
            upgraded = True
        elif isinstance(val, dict):
            # ensure role exists
            if "role" not in val:
                val["role"] = "student"
                upgraded = True
    if upgraded:
        save_users(users)
    return users

def save_users(data):
    with open(USERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------- Quizzes helpers ----------
def load_quizzes():
    try:
        with open(QUIZ_FILE, "r") as f:
            quizzes = json.load(f)
    except:
        quizzes = {}

    # Normalize results: accept old formats and convert to {"student":..., "score":...}
    for code, q in quizzes.items():
        fixed_results = []
        for r in q.get("results", []):
            if isinstance(r, dict):
                # r might have "name" or "student"
                student = r.get("student") or r.get("name") or r.get("username") or "Unknown"
                score = int(r.get("score", 0))
                fixed_results.append({"student": student, "score": score})
            else:
                # if it's a raw number or string, convert
                fixed_results.append({"student": "Unknown", "score": int(r) if isinstance(r, (int, float, str)) and str(r).isdigit() else 0})
        q["results"] = fixed_results
    return quizzes

def save_quizzes(data):
    with open(QUIZ_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------- API helper ----------
def fetch_questions_from_api(n=8, category=None, difficulty=None):
    url = f"https://opentdb.com/api.php?amount={n}&type=multiple"
    if category:
        url += f"&category={category}"
    if difficulty:
        url += f"&difficulty={difficulty}"
    data = requests.get(url).json()
    questions = []
    for item in data.get("results", []):
        q_text = html.unescape(item["question"])
        correct_ans = html.unescape(item["correct_answer"])
        options = [html.unescape(opt) for opt in item["incorrect_answers"]] + [correct_ans]
        random.shuffle(options)
        correct_label = ['A','B','C','D'][options.index(correct_ans)]
        questions.append({"q": q_text, "options": options, "correct": correct_label})
    return questions

# ---------- Auth decorators ----------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.url))
        return fn(*args, **kwargs)
    return wrapper

def teacher_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "username" not in session or session.get("role") != "teacher":
            return "Access denied: Teachers only!", 403
        return fn(*args, **kwargs)
    return wrapper

# ---------- Routes ----------
@app.route("/")
def home():
    return render_template("home.html")
@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        role = request.form.get("role", "student").strip().lower()

        users = load_users()
        if username in users:
            return "Username already exists!"

        users[username] = {
            "password": generate_password_hash(password),
            "role": role
        }
        save_users(users)
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        users = load_users()
        user = users.get(username)

        if user and check_password_hash(user["password"], password):
            session["username"] = username
            session["role"] = user.get("role", "student")  # default role if missing
            return redirect(url_for("profile"))
        else:
            return "Invalid username or password!"
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/profile")
@login_required
def profile():
    quizzes = load_quizzes()
    attempted = []
    created = []

    for code, quiz in quizzes.items():
        if quiz.get("created_by") == session["username"]:
            created.append({
                "title": quiz["title"],
                "code": code,
                "time_limit": quiz.get("time_limit", 0)
            })
        for result in quiz.get("results", []):
            if result.get("student") == session["username"]:  # ✅ changed "name" → "student"
                attempted.append({
                    "title": quiz["title"],
                    "score": result.get("score", 0)
                })

    best_attempts = sorted(attempted, key=lambda x: x["score"], reverse=True)[:10]

    return render_template(
        "profile.html",
        username=session["username"],
        role=session["role"],
        created_quizzes=created,
        attempted_quizzes=attempted,
        best10=best_attempts,
        total_created=len(created),
        total_attempted=len(attempted)
    )



# Teacher create manual quiz
@app.route("/create_quiz", methods=["GET","POST"])
@teacher_required
def create_quiz():
    if request.method == "POST":
        title = request.form.get("title","Untitled").strip()
        time_limit = int(request.form.get("time_limit",0))
        questions = []
        i = 1
        while f"q{i}" in request.form:
            qtxt = request.form.get(f"q{i}","").strip()
            opt_a = request.form.get(f"opt_a{i}","").strip()
            opt_b = request.form.get(f"opt_b{i}","").strip()
            opt_c = request.form.get(f"opt_c{i}","").strip()
            opt_d = request.form.get(f"opt_d{i}","").strip()
            correct = request.form.get(f"correct{i}","A").strip().upper()
            if correct not in ("A","B","C","D"): correct = "A"
            questions.append({"q": qtxt, "options":[opt_a,opt_b,opt_c,opt_d], "correct": correct})
            i += 1

        code = str(random.randint(1000,9999))
        quizzes = load_quizzes()
        quizzes[code] = {"title":title, "questions":questions, "results":[], "created_by":session["username"], "time_limit":time_limit}
        save_quizzes(quizzes)
        return f"Quiz created! Code: {code}"
    return render_template("create_quiz.html")

# Teacher AI quiz
@app.route("/create_ai_quiz", methods=["GET","POST"])
@teacher_required
def create_ai_quiz():
    if request.method == "POST":
        num_q = max(1, min(15, int(request.form.get("num_q",8))))
        category = request.form.get("category","")
        difficulty = request.form.get("difficulty","")
        time_limit = int(request.form.get("time_limit",0))

        questions = fetch_questions_from_api(num_q, category, difficulty)
        code = str(random.randint(1000,9999))
        quizzes = load_quizzes()
        quizzes[code] = {"title":f"AI Quiz ({num_q})", "questions":questions, "results":[], "created_by":session["username"], "time_limit":time_limit}
        save_quizzes(quizzes)
        return f"AI Quiz created! Code: {code}"
    return render_template("create_ai_quiz.html")

# Student join
@app.route("/join_quiz", methods=["GET","POST"])
def join_quiz():
    if request.method == "POST":
        code = request.form.get("code","").strip()
        quizzes = load_quizzes()
        if code in quizzes:
            # store guest name if no login
            if "username" not in session:
                session["username"] = request.form.get("name", "Guest")
                session["role"] = "guest"
            return redirect(url_for("take_quiz", code=code))
        return "Invalid code", 400
    return render_template("join_quiz.html")


# Student take (start confirmed via start page in earlier version)
@app.route("/take_quiz/<code>", methods=["GET","POST"])
@login_required
def take_quiz(code):
    quizzes = load_quizzes()
    qz = quizzes.get(code)
    if not qz:
        return "Quiz not found", 404

    if request.method == "POST":
        score = 0
        for i, q in enumerate(qz["questions"], 1):
            ans = request.form.get(f"q{i}","")
            if ans == q.get("correct"):
                score += 1
        qz["results"].append({"student": session["username"], "score": score})
        save_quizzes(quizzes)
        return redirect(url_for("result", code=code))

    return render_template("take_quiz.html", title=qz.get("title","Quiz"), questions=qz.get("questions",[]), time_limit=qz.get("time_limit",0), code=code)

@app.route("/result/<code>")
@login_required
def result(code):
    quizzes = load_quizzes()
    qz = quizzes.get(code)
    if not qz:
        return "Quiz not found", 404
    leaderboard = sorted(qz["results"], key=lambda r: r["score"], reverse=True)[:3]
    my_score = None
    for r in qz["results"]:
        if r.get("student") == session.get("username"):
            my_score = r.get("score")
            break
    return render_template("result.html", title=qz.get("title","Quiz"), leaderboard=leaderboard, my_score=my_score)

if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000)
  # ensure files exist
    if not os.path.exists(USERS_FILE):
        save_users({})
    if not os.path.exists(QUIZ_FILE):
        save_quizzes({})
    app.run(debug=True)
