from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime
from functools import wraps
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Google Sheets Setup
SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

creds_json = os.environ.get("GOOGLE_CREDENTIALS")
if creds_json:
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
else:
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPE)

client = gspread.authorize(creds)
sheet = client.open("Tuition_Master_Database")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
@login_required
def home():
    # FIX FOR RENDER ERROR: Passing user dictionary to dashboard.html
    user_info = {"user_name": session.get('username', 'Admin')}
    return render_template('dashboard.html', user=user_info)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        session['logged_in'] = True
        session['username'] = request.form.get('username', 'Admin')
        return redirect(url_for('home'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/get_classes')
def get_classes():
    try:
        wks = sheet.worksheet("Students")
        records = wks.get_all_records()
        classes = sorted(list(set([str(r.get('Class', '')).strip() for r in records if r.get('Class')])))
        return jsonify({"classes": classes})
    except Exception as e:
        return jsonify({"classes": []})

@app.route('/add_student', methods=['POST'])
def add_student():
    try:
        data = request.form
        wks = sheet.worksheet("Students")
        
        # Valid non-empty rows count
        all_vals = [r for r in wks.get_all_values() if any(r)]
        next_id = f"S{len(all_vals):03d}"
        
        name = data.get('name') or data.get('full_name') or ''
        student_class = data.get('class') or data.get('student_class') or ''
        contact = data.get('contact') or data.get('parent_contact') or ''
        fee = data.get('fee') or data.get('monthly_fee') or ''
        
        new_row = [
            next_id,            # Col A: Student_ID
            name,               # Col B: Full_Name
            student_class,      # Col C: Class
            contact,            # Col D: Parent_Contact
            fee,                # Col E: Monthly_Fee
            "Active",           # Col F: Status
            datetime.now().strftime("%Y-%m-%d") # Col G: Joining_Date
        ]
        
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return jsonify({"status": "success", "message": "Student Added Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
