from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
import gspread
from google.oauth2.service_account import Credentials
import os
from datetime import datetime, timedelta
from functools import wraps
import json
import geopy.distance
import pytz

# 1. Initialize Flask App once
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tuition_app_secret_key_2026')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)


# Serve App Icon
@app.route('/icon.jpeg')
def serve_icon():
    return send_from_directory('.', 'icon.jpeg')


# 2. Google Sheets Authentication
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds_json = os.environ.get("GOOGLE_CREDENTIALS")

if creds_json:
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=SCOPE
    )
else:
    creds = Credentials.from_service_account_file(
        "credentials.json",
        scopes=SCOPE
    )

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


# Decorator for Role Protection
def role_required(*allowed_roles):

    def decorator(f):

        @wraps(f)
        def decorated_function(*args, **kwargs):

            if 'logged_in' not in session:
                return redirect(url_for('login'))

            user_role = session.get('role', '').strip()

            if user_role not in allowed_roles:
                return render_template('unauthorized.html'), 403

            return f(*args, **kwargs)

        return decorated_function

    return decorator

# --------------------------------------------------
# TEACHER PERMISSION SYSTEM
# --------------------------------------------------

def get_current_teacher():
    """
    Logged-in user ka Teacher_Master record return karta hai.
    Admin ke liye None return karega.
    """

    email = str(
        session.get('email', '')
    ).strip().lower()

    if not email:
        return None

    try:
        teacher_sheet = sheet.worksheet("Teacher_Master")
        teachers = teacher_sheet.get_all_records()

        for row in teachers:

            row_email = str(
                row.get('Email', '')
            ).strip().lower()

            if row_email == email:
                return row

    except Exception as e:
        print(
            f"Error in get_current_teacher: {str(e)}"
        )

    return None


def teacher_has_permission(permission_name):
    """
    Check karta hai ki current user ko specific feature
    access karne ki permission hai ya nahi.
    """

    # Admin ko automatically full access
    if session.get('role') == 'Admin':
        return True

    teacher = get_current_teacher()

    if not teacher:
        return False

    teacher_id = str(
        teacher.get('Teacher_id', '')
    ).strip()

    if not teacher_id:
        return False

    try:

        permission_sheet = sheet.worksheet(
            "Teacher_Permissions"
        )

        permissions = (
            permission_sheet.get_all_records()
        )

        for row in permissions:

            row_teacher_id = str(
                row.get('Teacher_ID', '')
            ).strip()

            if row_teacher_id == teacher_id:

                value = str(
                    row.get(permission_name, 'No')
                ).strip().lower()

                return value in [
                    'yes',
                    'true',
                    '1',
                    'allowed'
                ]

    except Exception as e:

        print(
            f"Permission check error: {str(e)}"
        )

        return False

    # Permission row nahi mila
    return False


def permission_required(permission_name):

    """
    Page/API ko specific permission se protect karta hai.
    """

    def decorator(f):

        @wraps(f)
        def decorated_function(*args, **kwargs):

            if 'logged_in' not in session:
                return redirect(
                    url_for('login')
                )

            if not teacher_has_permission(
                permission_name
            ):

                return render_template(
                    'unauthorized.html'
                ), 403

            return f(*args, **kwargs)

        return decorated_function

    return decorator

# --------------------------------------------------
# PAGE ROUTES
# --------------------------------------------------

@app.route('/')
@login_required
def home():

    user_info = {
        "user_name": session.get('username', 'User'),
        "email": session.get('email', '')
    }

    if session.get('role') == 'Teacher':
        return render_template(
            'teacher_portal.html',
            user=user_info
        )

    return render_template(
        'dashboard.html',
        user=user_info
    )


@app.route('/login', methods=['GET', 'POST'])
def login():

    error_msg = None

    if request.method == 'POST':

        email = (
            request.form.get('email')
            or request.form.get('username')
            or ''
        ).strip().lower()

        try:

            wks = sheet.worksheet("Teacher_Master")
            records = wks.get_all_records()

            matched_user = None

            for row in records:

                if str(
                    row.get('Email', '')
                ).strip().lower() == email:

                    matched_user = row
                    break

            if matched_user:

                session.permanent = True
                session['logged_in'] = True
                session['email'] = email
                session['username'] = (
                    matched_user.get('Full_Name')
                    or email
                )

                role_val = str(
                    matched_user.get('Role', '')
                ).strip().lower()

                session['role'] = (
                    'Teacher'
                    if role_val == 'teacher'
                    else 'Admin'
                )

                return redirect(url_for('home'))

            else:

                error_msg = (
                    "Access Denied: Email not registered "
                    "in Teacher_Master database!"
                )

        except Exception as e:

            error_msg = f"Database Error: {str(e)}"

    return render_template(
        'login.html',
        error=error_msg
    )


@app.route('/logout')
def logout():

    session.clear()

    return redirect(url_for('login'))


@app.route('/attendance')
@app.route('/attendance.html')
@permission_required('Attendance')
def attendance_page():
    return render_template('attendance.html')

@app.route('/fees')
@app.route('/fees.html')
@permission_required('Fees')
def fees_page():
    
    user_info = {
        "user_email": session.get(
            'email',
            session.get('username', 'Staff')
        )
    }

    return render_template(
        'fees.html',
        user=user_info
    )


@app.route('/marks')
@app.route('/marks.html')
@permission_required('Marks')
def marks_page():
    return render_template('marks.html')


@app.route('/students')
@app.route('/students.html')
@permission_required('Students')
def students_page():
    return render_template('students.html')


@app.route('/teacher_portal')
@app.route('/teacher_portal.html')
@login_required
def teacher_portal_page():

    user_info = {
        "user_name": session.get(
            'username',
            'Teacher'
        )
    }

    return render_template(
        'teacher_portal.html',
        user=user_info
    )


# --------------------------------------------------
# APIS FOR FRONTEND JS & DASHBOARD
# --------------------------------------------------

@app.route('/get_classes')
@app.route('/api/get_classes')
@login_required
def get_classes():

    try:

        wks = sheet.worksheet("Students")
        records = wks.get_all_records()

        classes = sorted(
            list(
                set(
                    [
                        str(r.get('Class', '')).strip()
                        for r in records
                        if r.get('Class')
                    ]
                )
            )
        )

        if not classes:
            classes = [
                f"Class {i}"
                for i in range(1, 13)
            ]

        return jsonify({
            "classes": classes
        })

    except Exception:

        default_classes = [
            f"Class {i}"
            for i in range(1, 13)
        ]

        return jsonify({
            "classes": default_classes
        })


@app.route('/get_students/<path:class_name>')
@login_required
def get_students_by_class(class_name):

    try:

        wks = sheet.worksheet("Students")
        records = wks.get_all_records()

        filtered_students = []

        target = (
            str(class_name)
            .replace("Class", "")
            .strip()
            .lower()
        )

        for r in records:

            sheet_cls = str(
                r.get('Class', '')
            ).strip()

            sheet_cls_clean = (
                sheet_cls
                .replace("Class", "")
                .strip()
                .lower()
            )

            if (
                sheet_cls.lower()
                == str(class_name).strip().lower()
                or (
                    target
                    and target == sheet_cls_clean
                )
                or (
                    target
                    and target in sheet_cls_clean
                )
            ):

                filtered_students.append({
                    "Student_ID": str(
                        r.get('Student_ID', '')
                    ),
                    "Full_Name": str(
                        r.get('Full_Name', '')
                    )
                })

        return jsonify({
            "students": filtered_students
        })

    except Exception:

        return jsonify({
            "students": []
        })


@app.route('/add_student', methods=['POST'])
@permission_required('Students')
def add_student():

    try:

        data = (
            request.get_json(silent=True)
            or request.form
        )

        wks = sheet.worksheet("Students")

        all_vals = [
            r
            for r in wks.get_all_values()
            if any(r)
        ]

        next_id = f"S{len(all_vals):03d}"

        name = (
            data.get('full_name')
            or data.get('name')
            or data.get('studentName')
            or ''
        )

        student_class = (
            data.get('class_name')
            or data.get('class')
            or data.get('student_class')
            or ''
        )

        contact = (
            data.get('parent_contact')
            or data.get('contact')
            or data.get('phone')
            or ''
        )

        fee = (
            data.get('monthly_fee')
            or data.get('fee')
            or ''
        )

        joining_date = (
            data.get('joining_date')
            or datetime.now().strftime("%Y-%m-%d")
        )

        new_row = [
            next_id,
            name,
            student_class,
            contact,
            fee,
            "Active",
            joining_date
        ]

        wks.append_row(
            new_row,
            value_input_option='USER_ENTERED'
        )

        return jsonify({
            "status": "success",
            "message": "Student Added Successfully!"
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/students')
@permission_required('Students')
def get_students():

    try:

        wks = sheet.worksheet("Students")

        records = wks.get_all_records()

        students = []

        for row in records:

            student_id = str(
                row.get('Student_ID', '')
            ).strip()

            if not student_id:
                continue

            students.append({

                'id': student_id,

                'name': str(
                    row.get('Full_Name', '')
                ).strip(),

                'class': str(
                    row.get('Class', '')
                ).strip(),

                'contact': str(
                    row.get('Parent_Contact', '')
                ).strip(),

                'fee': str(
                    row.get('Monthly_Fee', '')
                ).strip(),

                'status': str(
                    row.get('Status', 'Active')
                ).strip(),

                'joining_date': str(
                    row.get('Joining_Date', '')
                ).strip()

            })

        return jsonify({
            'status': 'success',
            'students': students
        })

    except Exception as e:

        print(
            f"Error in get_students: {str(e)}"
        )

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/save_fee', methods=['POST'])
@permission_required('Fees')
def save_fee():

    try:

        data = (
            request.get_json(silent=True)
            or request.form
        )

        wks = sheet.worksheet("Fees_Log")

        all_vals = [
            r
            for r in wks.get_all_values()
            if any(r)
        ]

        fee_id = f"F{len(all_vals):03d}"

        now_ts = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        pay_date = (
            data.get('payment_date')
            or datetime.now().strftime("%Y-%m-%d")
        )

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

        wks.append_row(
            new_row,
            value_input_option='USER_ENTERED'
        )

        return jsonify({
            "status": "success",
            "message": "Fee Recorded Successfully!"
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/save_attendance', methods=['POST'])
@permission_required('Attendance')
def save_attendance():
    try:

        data = (
            request.get_json(
                force=True,
                silent=True
            )
            or request.form
            or {}
        )

        wks = sheet.worksheet(
            "Attendance_Log"
        )

        records = (
            data.get('attendance_data')
            or data.get('records')
            or data.get('students')
            or data.get('data')
            or []
        )

        if isinstance(data, list):
            records = data

        class_name = (
            data.get('class_name')
            or data.get('className')
            or data.get('class')
            or ''
        )

        att_date = (
            data.get('date')
            or datetime.now().strftime("%Y-%m-%d")
        )

        marked_by = session.get(
            'email',
            'Teacher'
        )

        now_ts = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        if not records:

            try:

                raw_body = json.loads(
                    request.data
                )

                records = raw_body.get(
                    'attendance_data',
                    raw_body.get(
                        'records',
                        []
                    )
                )

            except:

                pass

        if not records:

            return jsonify({
                "status": "error",
                "message":
                    "Payload Empty! Browser Refresh "
                    "(Ctrl+F5) karke dubara try karein."
            }), 400

        all_vals = [
            r
            for r in wks.get_all_values()
            if any(r)
        ]

        counter = len(all_vals)

        rows_to_append = []

        for item in records:

            counter += 1

            log_id = (
                f"ATT-"
                f"{att_date.replace('-', '')}-"
                f"{counter:03d}"
            )

            s_id = (
                item.get('student_id')
                or item.get('Student_ID')
                or item.get('id')
                or ''
            )

            s_name = (
                item.get('student_name')
                or item.get('Student_Name')
                or item.get('name')
                or ''
            )

            status = (
                item.get('status')
                or item.get('Status')
                or 'Present'
            )

            item_cls = (
                item.get('class_name')
                or item.get('class')
                or class_name
            )

            rows_to_append.append([
                log_id,
                att_date,
                item_cls,
                s_id,
                s_name,
                status,
                marked_by,
                now_ts,
                "Unlocked"
            ])

        if rows_to_append:

            wks.append_rows(
                rows_to_append,
                value_input_option='USER_ENTERED'
            )

            return jsonify({
                "status": "success",
                "message":
                    f"{len(rows_to_append)} "
                    "Students ki Attendance save ho gayi!"
            })

        else:

            return jsonify({
                "status": "error",
                "message":
                    "No valid rows generated"
            }), 400

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/save_marks', methods=['POST'])
@permission_required('Marks')
def save_marks():
    try:

        data = (
            request.get_json(
                force=True,
                silent=True
            )
            or request.form
            or {}
        )

        wks = sheet.worksheet(
            "Marks_Log"
        )

        class_name = data.get(
            'class_name',
            ''
        )

        subject = data.get(
            'subject',
            ''
        )

        test_title = data.get(
            'test_title',
            ''
        )

        test_date = data.get(
            'test_date',
            datetime.now().strftime("%Y-%m-%d")
        )

        try:

            total_marks = float(
                data.get(
                    'total_marks',
                    0
                )
            )

        except:

            total_marks = 0.0

        teacher_email = session.get(
            'email',
            data.get(
                'teacher_email',
                ''
            )
        )

        marks_list = (
            data.get('marks_data')
            or data.get('students')
            or []
        )

        now_ts = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        all_vals = [
            r
            for r in wks.get_all_values()
            if any(r)
        ]

        counter = len(all_vals)

        rows_to_append = []

        for m in marks_list:

            counter += 1

            mark_id = (
                f"MRK-"
                f"{test_date.replace('-', '')}-"
                f"{counter:03d}"
            )

            s_id = (
                m.get('student_id')
                or m.get('Student_ID')
                or ''
            )

            s_name = (
                m.get('student_name')
                or m.get('Student_Name')
                or ''
            )

            try:

                obtained = float(
                    m.get('obtained_marks')
                    or m.get('marks')
                    or 0
                )

            except:

                obtained = 0.0

            percentage = (
                round(
                    (obtained / total_marks) * 100,
                    2
                )
                if total_marks > 0
                else 0.0
            )

            remarks = (
                m.get('remarks')
                or ''
            )

            rows_to_append.append([
                mark_id,
                test_date,
                class_name,
                subject,
                test_title,
                s_id,
                s_name,
                total_marks,
                obtained,
                f"{percentage}%",
                remarks,
                teacher_email,
                now_ts,
                "Unlocked"
            ])

        if rows_to_append:

            wks.append_rows(
                rows_to_append,
                value_input_option='USER_ENTERED'
            )

            return jsonify({
                "status": "success",
                "message":
                    "Marks Uploaded Successfully!"
            })

        else:

            return jsonify({
                "status": "error",
                "message":
                    "No marks records received!"
            }), 400

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# --------------------------------------------------
# ADMIN SUMMARY
# --------------------------------------------------

@app.route('/api/admin_summary')
@role_required('Admin')
def admin_summary():

    try:

        att_sheet = sheet.worksheet(
            "Attendance_Log"
        )

        att_records = (
            att_sheet.get_all_records()
        )

        absent_list = []
        today_present = 0
        today_absent = 0

        if att_records:

            latest_date = str(
                att_records[-1].get(
                    'Date',
                    ''
                )
            ).strip()

            for row in att_records:

                row_date = str(
                    row.get(
                        'Date',
                        ''
                    )
                ).strip()

                if row_date == latest_date:

                    status = str(
                        row.get(
                            'Status',
                            ''
                        )
                    ).strip().lower()

                    if status == 'absent':

                        today_absent += 1

                        absent_list.append({
                            'student_id':
                                row.get('Student_ID'),

                            'name':
                                row.get('Student_Name'),

                            'class':
                                row.get('Class')
                        })

                    elif status == 'present':

                        today_present += 1

        marks_sheet = sheet.worksheet(
            "Marks_Log"
        )

        marks_records = (
            marks_sheet.get_all_records()
        )

        recent_marks = []

        for row in marks_records[-10:]:

            recent_marks.append({
                'date':
                    row.get('Date'),

                'class':
                    row.get('Class'),

                'subject':
                    row.get('Subject'),

                'test_title':
                    row.get('Test_Title'),

                'name':
                    row.get('Student_Name'),

                'obtained':
                    row.get('Obtained_Marks'),

                'total':
                    row.get('Total_Marks')
            })

        fees_sheet = sheet.worksheet(
            "Fees_Log"
        )

        fees_records = (
            fees_sheet.get_all_records()
        )

        recent_fees = []
        total_collection = 0

        for row in fees_records:

            amt = float(
                str(
                    row.get(
                        'Amount_Paid',
                        0
                    )
                )
                .replace('₹', '')
                .replace(',', '')
                .strip()
                or 0
            )

            total_collection += amt

        for row in fees_records[-10:]:

            recent_fees.append({
                'date':
                    row.get('Payment_Date'),

                'name':
                    row.get('Student_Name'),

                'class':
                    row.get('Class'),

                'amount':
                    row.get('Amount_Paid'),

                'mode':
                    row.get('Payment_Mode')
            })

        return jsonify({
            'status': 'success',
            'today_present':
                today_present,

            'today_absent':
                today_absent,

            'total_collection':
                total_collection,

            'absent_students':
                absent_list,

            'recent_marks':
                list(reversed(recent_marks)),

            'recent_fees':
                list(reversed(recent_fees))
        })

    except Exception as e:

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500




# --------------------------------------------------
# TEACHER PERMISSIONS
# --------------------------------------------------

@app.route('/api/get_teacher_permissions')
@login_required
def get_teacher_permissions():

    try:

        teacher_id = str(
            request.args.get('teacher_id', '')
        ).strip()

        if not teacher_id:
            return jsonify({
                'status': 'error',
                'message': 'Teacher ID required'
            }), 400

        permissions_sheet = sheet.worksheet(
            "Teacher_Permissions"
        )

        records = permissions_sheet.get_all_records()

        default_permissions = {
            'Attendance': False,
            'Marks': False,
            'Add_Student': False,
            'Collect_Fee': False,
            'Students': False,
            'My_Classes': False
        }

        for row in records:

            row_teacher_id = str(
                row.get('Teacher_ID', '')
            ).strip()

            if row_teacher_id == teacher_id:

                permissions = {
                    'Attendance':
                        str(row.get('Attendance', '')).lower()
                        in ['true', 'yes', '1', 'allowed'],

                    'Marks':
                        str(row.get('Marks', '')).lower()
                        in ['true', 'yes', '1', 'allowed'],

                    'Add_Student':
                        str(row.get('Add_Student', '')).lower()
                        in ['true', 'yes', '1', 'allowed'],

                    'Collect_Fee':
                        str(row.get('Collect_Fee', '')).lower()
                        in ['true', 'yes', '1', 'allowed'],

                    'Students':
                        str(row.get('Students', '')).lower()
                        in ['true', 'yes', '1', 'allowed'],

                    'My_Classes':
                        str(row.get('My_Classes', '')).lower()
                        in ['true', 'yes', '1', 'allowed']
                }

                return jsonify({
                    'status': 'success',
                    'permissions': permissions
                })

        return jsonify({
            'status': 'success',
            'permissions': default_permissions
        })

    except Exception as e:

        print(
            f"Error in get_teacher_permissions: {str(e)}"
        )

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/save_teacher_permissions', methods=['POST'])
@login_required
def save_teacher_permissions():

    try:

        data = request.json or {}

        teacher_id = str(
            data.get('teacher_id', '')
        ).strip()

        permissions = data.get(
            'permissions',
            {}
        )

        if not teacher_id:

            return jsonify({
                'status': 'error',
                'message': 'Teacher ID required'
            }), 400

        permissions_sheet = sheet.worksheet(
            "Teacher_Permissions"
        )

        records = permissions_sheet.get_all_records()

        existing_row = None

        # Find existing teacher
        for index, row in enumerate(
            records,
            start=2
        ):

            row_teacher_id = str(
                row.get('Teacher_ID', '')
            ).strip()

            if row_teacher_id == teacher_id:

                existing_row = index
                break

        values = [

            teacher_id,

            bool(
                permissions.get(
                    'Attendance',
                    False
                )
            ),

            bool(
                permissions.get(
                    'Marks',
                    False
                )
            ),

            bool(
                permissions.get(
                    'Add_Student',
                    False
                )
            ),

            bool(
                permissions.get(
                    'Collect_Fee',
                    False
                )
            ),

            bool(
                permissions.get(
                    'Students',
                    False
                )
            ),

            bool(
                permissions.get(
                    'My_Classes',
                    False
                )
            )
        ]

        # Existing teacher → update
        if existing_row:

            permissions_sheet.update(
                f'A{existing_row}:G{existing_row}',
                [values]
            )

        # New teacher → create
        else:

            permissions_sheet.append_row(
                values,
                value_input_option='USER_ENTERED'
            )

        return jsonify({
            'status': 'success',
            'message':
                'Teacher permissions saved successfully'
        })

    except Exception as e:

        print(
            f"Error in save_teacher_permissions: {str(e)}"
        )

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
        
        
# --------------------------------------------------
# TEACHER ATTENDANCE
# --------------------------------------------------

@app.route('/api/get_teacher_attendance')
@login_required
def get_teacher_attendance():

    try:

        # Teacher Master se sabhi teachers
        teacher_sheet = sheet.worksheet(
            "Teacher_Master"
        )

        teacher_records = (
            teacher_sheet.get_all_records()
        )

        # Teacher Attendance Log
        log_sheet = sheet.worksheet(
            "Teacher_Attendance_Log"
        )

        log_records = (
            log_sheet.get_all_records()
        )

        # IST mein aaj ki date
        ist = pytz.timezone(
            'Asia/Kolkata'
        )

        now_ist = datetime.now(ist)

        today_date = now_ist.strftime(
            '%Y-%m-%d'
        )

        # Aaj present/absent marked teachers
        today_attendance = {}

        for log_row in log_records:

            log_date = str(
                log_row.get(
                    'Date',
                    ''
                )
            ).strip()

            log_teacher_id = str(
                log_row.get(
                    'Teacher_ID',
                    ''
                )
            ).strip()

            if (
                log_date == today_date
                and log_teacher_id
            ):

                today_attendance[
                    log_teacher_id
                ] = str(
                    log_row.get(
                        'Status',
                        'Present'
                    )
                ).strip()

        # Final teacher list
        teachers = []

        for index, row in enumerate(
            teacher_records,
            start=2
        ):

            t_id = str(
                row.get(
                    'Teacher_id',
                    ''
                )
            ).strip()

            t_name = str(
                row.get(
                    'Full_Name',
                    ''
                )
            ).strip()

            if t_id and t_name:

                # Aaj attendance log mein entry hai?
                if t_id in today_attendance:

                    attendance_status = (
                        today_attendance[t_id]
                    )

                else:

                    # Scan/attendance entry nahi hai
                    attendance_status = 'Absent'

                teachers.append({
                    'row_id':
                        index,

                    'id':
                        t_id,

                    'name':
                        t_name,

                    'role':
                        str(
                            row.get(
                                'Role',
                                ''
                            )
                        ).strip(),

                    'status':
                        attendance_status
                })

        return jsonify({
            'status': 'success',
            'teachers': teachers
        })

    except Exception as e:

        print(
            f"Error in get_teacher_attendance: {str(e)}"
        )

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/mark_teacher_attendance', methods=['POST'])
@role_required('Admin')
def mark_teacher_attendance():
    try:
        # Only Admin can manually change teacher attendance
        if session.get('role') != 'Admin':
            return jsonify({
                'status': 'error',
                'message': 'Sirf Admin teacher attendance change kar sakta hai.'
            }), 403

        data = request.json or {}

        row_id = data.get('row_id')
        status = str(data.get('status', '')).strip().title()

        if not row_id or status not in ['Present', 'Absent']:
            return jsonify({
                'status': 'error',
                'message': 'Invalid row_id ya attendance status.'
            }), 400

        # Teacher Master
        teacher_sheet = sheet.worksheet("Teacher_Master")

        # Get teacher information
        t_id = str(
            teacher_sheet.cell(int(row_id), 1).value or ''
        ).strip()

        t_name = str(
            teacher_sheet.cell(int(row_id), 2).value or ''
        ).strip()

        if not t_id or not t_name:
            return jsonify({
                'status': 'error',
                'message': 'Teacher ID ya Teacher Name nahi mila.'
            }), 404

        # IST date
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)

        today_date = now_ist.strftime('%Y-%m-%d')
        current_time = now_ist.strftime('%I:%M:%S %p')

        # Teacher Attendance Log
        log_sheet = sheet.worksheet("Teacher_Attendance_Log")
        log_records = log_sheet.get_all_records()

        entry_found = False

        # Find today's attendance for this teacher
        for idx, row in enumerate(log_records, start=2):

            sheet_date = str(
                row.get('Date', '')
            ).strip()

            sheet_tid = str(
                row.get('Teacher_ID', '')
            ).strip()

            if (
                sheet_date == today_date
                and sheet_tid == t_id
            ):
                # Update ONLY today's attendance row
                log_sheet.update_cell(
                    idx,
                    5,
                    status
                )

                entry_found = True
                break

        # No entry today → create one
        if not entry_found:
            log_sheet.append_row([
                today_date,
                t_id,
                t_name,
                current_time,
                status
            ])

        return jsonify({
            'status': 'success',
            'message': f'{t_name} ki attendance {status} mark ho gayi.',
            'teacher_id': t_id,
            'teacher_name': t_name,
            'attendance_status': status,
            'date': today_date
        })

    except Exception as e:
        print(
            f"Error in mark_teacher_attendance: {str(e)}"
        )

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
        
@app.route('/api/teacher/my_classes')
@permission_required('My_Classes')
def teacher_my_classes():
    try:
        user_email = str(
            session.get('email', '')
        ).strip().lower()

        if not user_email:
            return jsonify({
                'status': 'error',
                'message': 'Teacher login required'
            }), 401

        # Teacher_Master se logged-in teacher ki details
        teacher_sheet = sheet.worksheet("Teacher_Master")
        teachers = teacher_sheet.get_all_records()

        teacher_info = None

        for row in teachers:
            row_email = str(
                row.get('Email', '')
            ).strip().lower()

            if row_email == user_email:
                teacher_info = row
                break

        if not teacher_info:
            return jsonify({
                'status': 'error',
                'message': 'Teacher record not found'
            }), 404

        teacher_id = str(
            teacher_info.get('Teacher_id', '')
        ).strip()

        if not teacher_id:
            return jsonify({
                'status': 'error',
                'message': 'Teacher ID missing'
            }), 400

        # Teacher_Assignments sheet
        assignment_sheet = sheet.worksheet(
            "Teacher_Assignments"
        )

        assignments = (
            assignment_sheet.get_all_records()
        )

        my_classes = []

        for row in assignments:

            row_teacher_id = str(
                row.get('Teacher_Id', '')
            ).strip()

            status = str(
                row.get('Status', 'Active')
            ).strip().lower()

            if (
                row_teacher_id == teacher_id
                and status == 'active'
            ):
                my_classes.append({
                    'assignment_id': str(
                        row.get(
                            'Assignment_ID',
                            ''
                        )
                    ).strip(),

                    'branch': str(
                        row.get(
                            'Branch',
                            ''
                        )
                    ).strip(),

                    'class': str(
                        row.get(
                            'Class',
                            ''
                        )
                    ).strip(),

                    'medium': str(
                        row.get(
                            'Medium',
                            ''
                        )
                    ).strip(),

                    'subject': str(
                        row.get(
                            'Subject',
                            ''
                        )
                    ).strip()
                })

        return jsonify({
            'status': 'success',
            'teacher_id': teacher_id,
            'teacher_name': str(
                teacher_info.get(
                    'Full_Name',
                    ''
                )
            ).strip(),
            'classes': my_classes
        })

    except Exception as e:

        print(
            f"Error in teacher_my_classes: {str(e)}"
        )

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# --------------------------------------------------
# MULTIPLE TUITION BRANCHES
# --------------------------------------------------

BRANCHES = [

    {
        "name": "Branch 1",
        "lat": 22.9984656,
        "lon": 72.6409959
    },

    {
        "name": "Branch 2",
        "lat": 23.0010259,
        "lon": 72.6266719
    }

]


# --------------------------------------------------
# QR TEACHER ATTENDANCE
# --------------------------------------------------

@app.route('/api/scan_qr_attendance', methods=['POST'])
@permission_required('Scanner')
def scan_qr_attendance():

    try:

        data = request.json or {}

        # Logged-in teacher ka email
        user_email = (
            session.get('email')
            or data.get('email')
        )

        if not user_email:
            return jsonify({
                'status': 'error',
                'message':
                    'Aap logged in nahi hain! '
                    'Kripya pehle login karein.'
            }), 401

        # ------------------------------------------
        # LOCATION DATA
        # ------------------------------------------

        user_lat = data.get('lat')
        user_lon = data.get('lon')
        gps_accuracy = data.get('accuracy')

        if user_lat is None or user_lon is None:
            return jsonify({
                'status': 'error',
                'message':
                    'Location access allow kijiye!'
            }), 400

        try:

            user_lat = float(user_lat)
            user_lon = float(user_lon)

            if gps_accuracy is not None:
                gps_accuracy = float(gps_accuracy)

        except (TypeError, ValueError):

            return jsonify({
                'status': 'error',
                'message':
                    'Invalid GPS location data mila.'
            }), 400

        # ------------------------------------------
        # CHECK BRANCH LOCATION
        # ------------------------------------------

        user_loc = (
            user_lat,
            user_lon
        )

        valid_branch = False
        nearest_distance = None
        nearest_branch = None

        for branch in BRANCHES:

            branch_loc = (
                float(branch['lat']),
                float(branch['lon'])
            )

            distance = geopy.distance.geodesic(
                branch_loc,
                user_loc
            ).meters

            if (
                nearest_distance is None
                or distance < nearest_distance
            ):
                nearest_distance = distance
                nearest_branch = branch
                
                if distance <= 50 and (
                    gps_accuracy is None or gps_accuracy <= 50
                ):
                    valid_branch = True
                    break

        # ------------------------------------------
        # LOCATION NOT VALID
        # ------------------------------------------

        if not valid_branch:

            distance_text = (
                f"{nearest_distance:.1f}m"
                if nearest_distance is not None
                else "unknown"
            )

            accuracy_text = (
                f"{gps_accuracy:.1f}m"
                if gps_accuracy is not None
                else "unknown"
            )

            return jsonify({
                'status': 'error',
                'message': (
                    f'Tuition branch se aapki location '
                    f'approx {distance_text} door mili. '
                    f'GPS accuracy ±{accuracy_text} hai. '
                    f'Please branch ke andar/near jaakar '
                    f'GPS dobara try karein.'
                ),
                'distance': nearest_distance,
                'gps_accuracy': gps_accuracy
            }), 400

        # ------------------------------------------
        # IST DATE AND TIME
        # ------------------------------------------

        ist = pytz.timezone(
            'Asia/Kolkata'
        )

        now_ist = datetime.now(ist)

        today_date = now_ist.strftime(
            '%Y-%m-%d'
        )

        current_time = now_ist.strftime(
            '%I:%M:%S %p'
        )

        # ------------------------------------------
        # FIND TEACHER
        # ------------------------------------------

        teacher_sheet = sheet.worksheet(
            "Teacher_Master"
        )

        teachers = (
            teacher_sheet.get_all_records()
        )

        teacher_info = None

        for row in teachers:

            row_email = str(
                row.get(
                    'Email',
                    ''
                )
            ).strip().lower()

            if (
                row_email
                == str(
                    user_email
                ).strip().lower()
            ):

                teacher_info = row
                break

        if not teacher_info:

            return jsonify({
                'status': 'error',
                'message':
                    'Aapki Email Teacher '
                    'Database mein nahi mili!'
            }), 403

        t_id = str(
            teacher_info.get(
                'Teacher_id',
                ''
            )
        ).strip()

        t_name = str(
            teacher_info.get(
                'Full_Name',
                ''
            )
        ).strip()

        if not t_id or not t_name:

            return jsonify({
                'status': 'error',
                'message':
                    'Teacher ID ya Teacher Name '
                    'missing hai!'
            }), 400

        # ------------------------------------------
        # TEACHER ATTENDANCE LOG
        # ------------------------------------------

        log_sheet = sheet.worksheet(
            "Teacher_Attendance_Log"
        )

        log_records = (
            log_sheet.get_all_records()
        )

        # ------------------------------------------
        # DUPLICATE CHECK
        # ------------------------------------------

        for row in log_records:

            sheet_date = str(
                row.get(
                    'Date',
                    ''
                )
            ).strip()

            sheet_teacher_id = str(
                row.get(
                    'Teacher_ID',
                    ''
                )
            ).strip()

            if (
                sheet_date == today_date
                and sheet_teacher_id == t_id
            ):

                return jsonify({
                    'status': 'success',
                    'already_marked': True,
                    'message':
                        f'{t_name}, aaj ki attendance '
                        'already Present hai.'
                })

        # ------------------------------------------
        # CREATE ATTENDANCE ENTRY
        # ------------------------------------------

        log_sheet.append_row([
            today_date,
            t_id,
            t_name,
            current_time,
            'Present'
        ])

        return jsonify({
            'status': 'success',
            'already_marked': False,
            'message':
                f'Attendance marked Present '
                f'for {t_name}!'
        })

    except Exception as e:

        print(
            f"Error in scan_qr_attendance: {str(e)}"
        )

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
        
        print(
            f"Error in scan_qr_attendance: {str(e)}"
        )

        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# --------------------------------------------------
# SCANNER PAGE
# --------------------------------------------------

@app.route('/scan')
@permission_required('Scanner')
def scan_page():
    return render_template('scan.html')
