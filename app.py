from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime, timedelta
from functools import wraps
import json
import geopy.distance

# 1. Initialize Flask App once
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tuition_app_secret_key_2026')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# Serve App Icon
@app.route('/icon.jpeg')
def serve_icon():
    return send_from_directory('.', 'icon.jpeg')

# 2. Google Sheets Authentication
SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

creds_json = os.environ.get("GOOGLE_CREDENTIALS")
if creds_json:
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
else:
    creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPE)

client = gspread.authorize(creds)
sheet = client.open("Tuition_Master_Database")

# Decorator for Login Protection
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- PAGE ROUTES ---

@app.route('/')
@login_required
def home():
    user_info = {"user_name": session.get('username', 'User'), "email": session.get('email', '')}
    if session.get('role') == 'Teacher':
        return render_template('teacher_portal.html', user=user_info)
    return render_template('dashboard.html', user=user_info)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error_msg = None
    if request.method == 'POST':
        email = (request.form.get('email') or request.form.get('username') or '').strip().lower()
        
        try:
            wks = sheet.worksheet("Teacher_Master")
            records = wks.get_all_records()
            
            matched_user = None
            for row in records:
                if str(row.get('Email', '')).strip().lower() == email:
                    matched_user = row
                    break
            
            if matched_user:
                session.permanent = True
                session['logged_in'] = True
                session['email'] = email
                session['username'] = matched_user.get('Full_Name') or email
                role_val = str(matched_user.get('Role', '')).strip().lower()
                session['role'] = 'Teacher' if role_val == 'teacher' else 'Admin'
                return redirect(url_for('home'))
            else:
                error_msg = "Access Denied: Email not registered in Teacher_Master database!"
                
        except Exception as e:
            error_msg = f"Database Error: {str(e)}"

    return render_template('login.html', error=error_msg)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/attendance')
@app.route('/attendance.html')
@login_required
def attendance_page():
    return render_template('attendance.html')
    
@app.route('/fees')
@app.route('/fees.html')
@login_required
def fees_page():
    user_info = {"user_email": session.get('email', session.get('username', 'Staff'))}
    return render_template('fees.html', user=user_info)

@app.route('/marks')
@app.route('/marks.html')
@login_required
def marks_page():
    return render_template('marks.html')

@app.route('/students')
@app.route('/students.html')
@login_required
def students_page():
    return render_template('students.html')

@app.route('/teacher_portal')
@app.route('/teacher_portal.html')
@login_required
def teacher_portal_page():
    user_info = {"user_name": session.get('username', 'Teacher')}
    return render_template('teacher_portal.html', user=user_info)

# --- APIS FOR FRONTEND JS & DASHBOARD ---

@app.route('/get_classes')
@app.route('/api/get_classes')
def get_classes():
    try:
        wks = sheet.worksheet("Students")
        records = wks.get_all_records()
        classes = sorted(list(set([str(r.get('Class', '')).strip() for r in records if r.get('Class')])))
        if not classes:
            classes = [f"Class {i}" for i in range(1, 13)]
        return jsonify({"classes": classes})
    except Exception as e:
        default_classes = [f"Class {i}" for i in range(1, 13)]
        return jsonify({"classes": default_classes})

@app.route('/get_students/<path:class_name>')
def get_students_by_class(class_name):
    try:
        wks = sheet.worksheet("Students")
        records = wks.get_all_records()
        filtered_students = []
        
        target = str(class_name).replace("Class", "").strip().lower()
        
        for r in records:
            sheet_cls = str(r.get('Class', '')).strip()
            sheet_cls_clean = sheet_cls.replace("Class", "").strip().lower()
            
            if sheet_cls.lower() == str(class_name).strip().lower() or (target and target == sheet_cls_clean) or (target in sheet_cls_clean):
                filtered_students.append({
                    "Student_ID": str(r.get('Student_ID', '')),
                    "Full_Name": str(r.get('Full_Name', ''))
                })
                
        return jsonify({"students": filtered_students})
    except Exception as e:
        return jsonify({"students": []})

@app.route('/add_student', methods=['POST'])
def add_student():
    try:
        data = request.get_json(silent=True) or request.form
        wks = sheet.worksheet("Students")
        
        all_vals = [r for r in wks.get_all_values() if any(r)]
        next_id = f"S{len(all_vals):03d}"
        
        name = data.get('full_name') or data.get('name') or data.get('studentName') or ''
        student_class = data.get('class_name') or data.get('class') or data.get('student_class') or ''
        contact = data.get('parent_contact') or data.get('contact') or data.get('phone') or ''
        fee = data.get('monthly_fee') or data.get('fee') or ''
        joining_date = data.get('joining_date') or datetime.now().strftime("%Y-%m-%d")
        
        new_row = [next_id, name, student_class, contact, fee, "Active", joining_date]
        
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return jsonify({"status": "success", "message": "Student Added Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/save_fee', methods=['POST'])
def save_fee():
    try:
        data = request.get_json(silent=True) or request.form
        wks = sheet.worksheet("Fees_Log")
        
        all_vals = [r for r in wks.get_all_values() if any(r)]
        fee_id = f"F{len(all_vals):03d}"
        
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pay_date = data.get('payment_date') or datetime.now().strftime("%Y-%m-%d")
        
        new_row = [
            fee_id,
            pay_date,
            data.get('student_id', ''),
            data.get('student_name', ''),
            data.get('class_name', ''),
            data.get('amount', ''),
            data.get('for_month', ''),
            data.get('payment_mode', ''),
            session.get('email', 'Staff'),
            now_ts
        ]
        
        wks.append_row(new_row, value_input_option='USER_ENTERED')
        return jsonify({"status": "success", "message": "Fee Recorded Successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/save_attendance', methods=['POST'])
def save_attendance():
    try:
        data = request.get_json(force=True, silent=True) or request.form or {}
        wks = sheet.worksheet("Attendance_Log")
        
        records = (
            data.get('attendance_data') or 
            data.get('records') or 
            data.get('students') or 
            data.get('data') or 
            []
        )
        
        if isinstance(data, list):
            records = data
            
        class_name = data.get('class_name') or data.get('className') or data.get('class') or ''
        att_date = data.get('date') or datetime.now().strftime("%Y-%m-%d")
        marked_by = session.get('email', 'Teacher')
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not records:
            try:
                raw_body = json.loads(request.data)
                records = raw_body.get('attendance_data', raw_body.get('records', []))
            except:
                pass

        if not records:
            return jsonify({
                "status": "error", 
                "message": "Payload Empty! Browser Refresh (Ctrl+F5) karke dubara try karein."
            }), 400

        all_vals = [r for r in wks.get_all_values() if any(r)]
        counter = len(all_vals)
        
        rows_to_append = []
        for item in records:
            counter += 1
            log_id = f"ATT-{att_date.replace('-', '')}-{counter:03d}"
            
            s_id = item.get('student_id') or item.get('Student_ID') or item.get('id') or ''
            s_name = item.get('student_name') or item.get('Student_Name') or item.get('name') or ''
            status = item.get('status') or item.get('Status') or 'Present'
            item_cls = item.get('class_name') or item.get('class') or class_name
            
            rows_to_append.append([
                log_id, att_date, item_cls, s_id, s_name, status, marked_by, now_ts, "Unlocked"
            ])
            
        if rows_to_append:
            wks.append_rows(rows_to_append, value_input_option='USER_ENTERED')
            return jsonify({"status": "success", "message": f"{len(rows_to_append)} Students ki Attendance save ho gayi!"})
        else:
            return jsonify({"status": "error", "message": "No valid rows generated"}), 400

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/save_marks', methods=['POST'])
def save_marks():
    try:
        data = request.get_json(force=True, silent=True) or request.form or {}
        wks = sheet.worksheet("Marks_Log")
        
        class_name = data.get('class_name', '')
        subject = data.get('subject', '')
        test_title = data.get('test_title', '')
        test_date = data.get('test_date', datetime.now().strftime("%Y-%m-%d"))
        
        try:
            total_marks = float(data.get('total_marks', 0))
        except:
            total_marks = 0.0
            
        teacher_email = session.get('email', data.get('teacher_email', ''))
        marks_list = data.get('marks_data') or data.get('students') or []
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        all_vals = [r for r in wks.get_all_values() if any(r)]
        counter = len(all_vals)
        
        rows_to_append = []
        for m in marks_list:
            counter += 1
            mark_id = f"MRK-{test_date.replace('-', '')}-{counter:03d}"
            
            s_id = m.get('student_id') or m.get('Student_ID') or ''
            s_name = m.get('student_name') or m.get('Student_Name') or ''
            
            try:
                obtained = float(m.get('obtained_marks') or m.get('marks') or 0)
            except:
                obtained = 0.0
                
            percentage = round((obtained / total_marks) * 100, 2) if total_marks > 0 else 0.0
            remarks = m.get('remarks') or ''
            
            rows_to_append.append([
                mark_id, test_date, class_name, subject, test_title, s_id, s_name, total_marks, obtained, f"{percentage}%", remarks, teacher_email, now_ts, "Unlocked"
            ])
            
        if rows_to_append:
            wks.append_rows(rows_to_append, value_input_option='USER_ENTERED')
            return jsonify({"status": "success", "message": "Marks Uploaded Successfully!"})
        else:
            return jsonify({"status": "error", "message": "No marks records received!"}), 400

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/admin_summary')
@login_required
def admin_summary():
    try:
        att_sheet = sheet.worksheet("Attendance_Log")
        att_records = att_sheet.get_all_records()
        
        absent_list = []
        today_present = 0
        today_absent = 0

        if att_records:
            latest_date = str(att_records[-1].get('Date', '')).strip()

            for row in att_records:
                row_date = str(row.get('Date', '')).strip()
                if row_date == latest_date:
                    status = str(row.get('Status', '')).strip().lower()
                    if status == 'absent':
                        today_absent += 1
                        absent_list.append({
                            'student_id': row.get('Student_ID'),
                            'name': row.get('Student_Name'),
                            'class': row.get('Class')
                        })
                    elif status == 'present':
                        today_present += 1

        marks_sheet = sheet.worksheet("Marks_Log")
        marks_records = marks_sheet.get_all_records()
        recent_marks = []
        for row in marks_records[-10:]:
            recent_marks.append({
                'date': row.get('Date'),
                'class': row.get('Class'),
                'subject': row.get('Subject'),
                'test_title': row.get('Test_Title'),
                'name': row.get('Student_Name'),
                'obtained': row.get('Obtained_Marks'),
                'total': row.get('Total_Marks')
            })

        fees_sheet = sheet.worksheet("Fees_Log")
        fees_records = fees_sheet.get_all_records()
        recent_fees = []
        total_collection = 0
        
        for row in fees_records:
            amt = float(str(row.get('Amount_Paid', 0)).replace('₹','').replace(',','').strip() or 0)
            total_collection += amt

        for row in fees_records[-10:]:
            recent_fees.append({
                'date': row.get('Payment_Date'),
                'name': row.get('Student_Name'),
                'class': row.get('Class'),
                'amount': row.get('Amount_Paid'),
                'mode': row.get('Payment_Mode')
            })

        return jsonify({
            'status': 'success',
            'today_present': today_present,
            'today_absent': today_absent,
            'total_collection': total_collection,
            'absent_students': absent_list,
            'recent_marks': list(reversed(recent_marks)),
            'recent_fees': list(reversed(recent_fees))
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/get_teacher_attendance')
@login_required
def get_teacher_attendance():
    try:
        teacher_sheet = sheet.worksheet("Teacher_Master")
        records = teacher_sheet.get_all_records()
        
        teachers = []
        for index, row in enumerate(records, start=2):
            t_id = str(row.get('Teacher_id', '')).strip()
            t_name = str(row.get('Full_Name', '')).strip()
            
            if t_id and t_name:
                teachers.append({
                    'row_id': index,
                    'id': t_id,
                    'name': t_name,
                    'role': str(row.get('Role', '')).strip(),
                    'status': str(row.get('Status', 'Active')).strip()
                })
        return jsonify({'status': 'success', 'teachers': teachers})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/mark_teacher_attendance', methods=['POST'])
@login_required

def mark_teacher_attendance():
    try:
        data = request.json or {}
        row_id = data.get('row_id')
        status = data.get('status')

        if not row_id or not status:
            return jsonify({'status': 'error', 'message': 'Missing row_id or status'}), 400

        # 1. Update status in Teacher_Master
        teacher_sheet = sheet.worksheet("Teacher_Master")
        teacher_sheet.update_cell(row_id, 5, status)

        # Fetch Teacher ID and Name from Column 1 and Column 2
        t_id = str(teacher_sheet.cell(row_id, 1).value or '').strip()
        t_name = str(teacher_sheet.cell(row_id, 2).value or '').strip()
        today_date = datetime.now().strftime("%Y-%m-%d")

        # 2. Update or Append entry in Teacher_Attendance_Log
        log_sheet = sheet.worksheet("Teacher_Attendance_Log")
        log_records = log_sheet.get_all_records()

        entry_found = False
        # Checking if attendance for today already exists for this teacher
        for idx, row in enumerate(log_records, start=2):
            sheet_date = str(row.get('Date', '')).strip()
            sheet_tid = str(row.get('Teacher_ID', '')).strip()

            if sheet_date == today_date and sheet_tid == t_id:
                log_sheet.update_cell(idx, 4, status)
                entry_found = True
                break

        # If no entry found for today, append new row
        if not entry_found:
            log_sheet.append_row([today_date, t_id, t_name, status])

        return jsonify({'status': 'success', 'message': 'Teacher attendance logged successfully'})

    except Exception as e:
        print(f"Error in mark_teacher_attendance: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Multiple Tuition Branches (Coordinates: Latitude, Longitude)
BRANCHES = [
    {"name": "Branch 1", "lat": 22.9985473, "lon": 72.6435501},  # Branch 1 Coordinates
    {"name": "Branch 2", "lat": 23.001270, "lon": 72.627132}   # Branch 2 Coordinates
]

@app.route('/api/scan_qr_attendance', methods=['POST'])
@login_required
def scan_qr_attendance():
    try:
        user_email = session.get('user_email')
        data = request.json or {}
        
        user_lat = data.get('lat')
        user_lon = data.get('lon')
        
        if not user_lat or not user_lon:
            return jsonify({'status': 'error', 'message': 'Location access allow kijiye!'}), 400

        # Check distance from both branches (30 Meters Radius Limit)
        user_loc = (user_lat, user_lon)
        valid_branch = False
        
        for branch in BRANCHES:
            branch_loc = (branch['lat'], branch['lon'])
            distance = geopy.distance.geodesic(branch_loc, user_loc).meters
            if distance <= 30: # Max 30 meters range
                valid_branch = True
                break
        
        if not valid_branch:
            return jsonify({'status': 'error', 'message': 'Aap kisi bhi Tuition Branch ke 30m range mein nahi hain!'}), 400

        today_date = datetime.now().strftime("%Y-%m-%d")
        
        # Get Teacher Info from Teacher_Master
        t_sheet = sheet.worksheet("Teacher_Master")
        teachers = t_sheet.get_all_records()
        
        teacher_info = None
        for row in teachers:
            if str(row.get('Email', '')).strip().lower() == str(user_email).strip().lower():
                teacher_info = row
                break
                
        if not teacher_info:
            return jsonify({'status': 'error', 'message': 'Aapki Email Teacher Database mein nahi mili!'}), 403

        t_sheet = sheet.worksheet("Teacher Master")
        t_name = teacher_info.get('Full_Name')

        # Check Duplicate Entry for Today
        log_sheet = sheet.worksheet("Teacher_Attendance_Log")
        log_records = log_sheet.get_all_records()
        
        for row in log_records:
            if str(row.get('Date')) == today_date and str(row.get('Teacher_ID')) == str(t_id):
                return jsonify({'status': 'error', 'message': 'Aaj ki attendance pehle se logged hai!'}), 400

        # Log Attendance
        log_sheet.append_row([today_date, t_id, t_name, 'Present'])
        
        return jsonify({'status': 'success', 'message': f'Attendance marked for {t_name}!'})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Scanner Page View Route
@app.route('/scan')
@login_required
def scan_page():
    return render_template('scan.html')
