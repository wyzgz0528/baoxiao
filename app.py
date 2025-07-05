from dotenv import load_dotenv
load_dotenv()  # 必须在读取os.environ之前

import os, re, uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, abort, send_file, make_response)
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO
from weasyprint import HTML

from models import db, User, Expense, ExpenseType

# ------------------- Flask 基本配置 -------------------
app = Flask(__name__)
os.makedirs('instance', exist_ok=True)                 # SQLite 文件目录
app.config['SECRET_KEY'] = 'your_secret_key'

# 数据库
if os.environ.get("USE_SQLSERVER"):
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("SQLALCHEMY_DATABASE_URI")
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///instance/baoxiao.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JSON_AS_ASCII'] = False                   # jsonify / render_template 中文不转义

# 上传目录
UPLOAD_FOLDER = Path('static/invoices')
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)

db.init_app(app)

# ------------------- 通用常量 -------------------
STATUS_PENDING   = '待审批'
STATUS_APPROVED  = '通过审批'
STATUS_REJECTED  = '驳回'

USERNAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{2,79}$')

# ------------------- 工具函数 -------------------
def validate_username(username: str):
    if not USERNAME_RE.match(username):
        abort(400, '用户名只能包含英文字母、数字、下划线，且以字母开头')

def secure_filename_cn(filename: str) -> str:
    """
    保留中文 / 数字 / 字母，剔除路径分隔符，最后附加一段 uuid 保证唯一。
    """
    filename = os.path.basename(filename)
    name, ext = os.path.splitext(filename)
    uid = uuid.uuid4().hex[:8]
    return f'{name}_{uid}{ext}'

def save_invoice(file_storage):
    """
    把上传的 FileStorage 保存到 static/invoices，返回保存后的文件名。
    传入 None 或空文件名则返回 None。
    """
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename_cn(file_storage.filename)
    file_path = UPLOAD_FOLDER / filename
    file_storage.save(file_path)
    return filename

# 金额数字转中文大写金额
def num_to_rmb_upper(num):
    """
    数字金额转中文大写金额（支持到分）
    1234.56 -> 壹仟贰佰叁拾肆元伍角陆分
    """
    units = ["元", "拾", "佰", "仟", "万", "拾", "佰", "仟", "亿"]
    nums = "零壹贰叁肆伍陆柒捌玖"
    fraction = ["角", "分"]
    head = ""
    if num < 0:
        head = "负"
        num = -num
    num = round(num + 0.0000001, 2)  # 防止浮点误差
    integer = int(num)
    decimal = int(round((num - integer) * 100))
    result = ""
    # 处理整数部分
    if integer == 0:
        result = "零元"
    else:
        unit_pos = 0
        zero = True
        while integer > 0:
            n = integer % 10
            if n == 0:
                if not zero:
                    result = nums[0] + result
                zero = True
            else:
                result = nums[n] + units[unit_pos] + result
                zero = False
            unit_pos += 1
            integer //= 10
    # 处理小数部分
    if decimal == 0:
        result += "整"
    else:
        jiao = decimal // 10
        fen = decimal % 10
        if jiao > 0:
            result += nums[jiao] + fraction[0]
        if fen > 0:
            result += nums[fen] + fraction[1]
    # 处理零元零角等冗余
    result = result.replace("零元", "元")
    result = result.replace("零角", "")
    result = result.replace("零分", "")
    result = result.replace("元整", "元整")
    if result.startswith("元"):
        result = "零" + result
    return head + result

import re

def safe_filename(name):
    # 替换所有空白字符（包括全角空格、制表符等）为下划线
    name = re.sub(r'\\s+', '_', name)
    # 替换所有非字母数字下划线为下划线
    name = re.sub(r'[^A-Za-z0-9_]', '_', name)
    return name

# --------------------------------------------------
#                     认证相关
# --------------------------------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        validate_username(username)

        realname = request.form['realname'].strip()
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
            return redirect(url_for('register'))

        user = User(username=username,
                    realname=realname,
                    password=generate_password_hash(password),
                    role='user')
        db.session.add(user)
        db.session.commit()
        flash('注册成功，请登录')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id']  = user.id
            session['role']     = user.role
            session['realname'] = user.realname
            flash('登录成功！')
            return redirect(url_for('index'))
        flash('用户名或密码错误')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('已退出登录')
    return redirect(url_for('login'))

# --------------------------------------------------
#                   首页 & 提交
# --------------------------------------------------
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', role=session.get('role'))

@app.route('/submit', methods=['GET', 'POST'])
def submit_expense():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    types = ExpenseType.query.all()

    if request.method == 'POST':
        expenses = []
        idx = 1
        while True:
            date_str = request.form.get(f'date_{idx}')
            type_id  = request.form.get(f'type_{idx}')
            title    = request.form.get(f'title_{idx}')
            amount   = request.form.get(f'amount_{idx}')
            description   = request.form.get(f'description_{idx}')
            invoice_file  = request.files.get(f'invoice_{idx}')

            # 已经没有更多条目时结束循环
            if not (date_str and type_id and title and amount):
                break

            # ------------- 后端再次校验：发票不能为空 -------------
            if not invoice_file or invoice_file.filename == '':
                flash(f'第 {idx} 条报销：发票不能为空', 'danger')
                return redirect(url_for('submit_expense'))

            invoice_filename = save_invoice(invoice_file)

            expense = Expense(
                date=datetime.strptime(date_str, '%Y-%m-%d').date(),
                type_id=int(type_id),
                title=title,
                amount=Decimal(amount),
                invoice=invoice_filename,
                description=description,
                submitter_id=session['user_id'],
                status=STATUS_PENDING
            )
            db.session.add(expense)
            expenses.append(expense)
            idx += 1

        db.session.commit()
        flash(f'成功提交 {len(expenses)} 条报销单！', 'success')
        return redirect(url_for('view_records'))

    return render_template('submit.html', types=types)

# --------------------------------------------------
#                  报销记录（本人）
# --------------------------------------------------
@app.route('/records')
def view_records():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    role = session.get('role')
    query = Expense.query.filter_by(submitter_id=session['user_id'])

    # ---------- 筛选 ----------
    start_date = request.args.get('start_date')
    end_date   = request.args.get('end_date')
    status     = request.args.get('status')
    type_id    = request.args.get('type')

    if start_date:
        try:
            query = query.filter(Expense.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
        except ValueError:
            pass
    if end_date:
        try:
            query = query.filter(Expense.date <= datetime.strptime(end_date, '%Y-%m-%d').date())
        except ValueError:
            pass
    if status:
        query = query.filter_by(status=status)
    if type_id:
        query = query.filter_by(type_id=int(type_id))

    # ---------- 排序 ----------
    sort = request.args.get('sort')
    if sort == 'date_asc':
        query = query.order_by(Expense.date.asc())
    elif sort == 'date_desc':
        query = query.order_by(Expense.date.desc())
    elif sort == 'amount_asc':
        query = query.order_by(Expense.amount.asc())
    elif sort == 'amount_desc':
        query = query.order_by(Expense.amount.desc())

    expenses = query.all()
    total    = sum(e.amount for e in expenses)
    types    = ExpenseType.query.all()
    return render_template('records.html', expenses=expenses, role=role,
                           total=total, users=[], types=types)

# --------------------------------------------------
#                   财务审批
# --------------------------------------------------
@app.route('/approve', methods=['GET', 'POST'])
def approve_expense():
    if 'user_id' not in session or session.get('role') != 'finance':
        flash('无权限')
        return redirect(url_for('login'))

    if request.method == 'POST':
        expense_id = request.form['expense_id']
        action     = request.form['action']
        expense    = Expense.query.get(expense_id)

        if not expense or expense.status != STATUS_PENDING:
            flash('报销单不存在或已处理')
            return redirect(url_for('approve_expense'))

        if action == 'approve':
            expense.status = STATUS_APPROVED
            expense.reject_reason = None
        elif action == 'reject':
            expense.status = STATUS_REJECTED
            expense.reject_reason = request.form.get('reject_reason', '').strip()
        expense.approver_id = session['user_id']
        db.session.commit()
        flash('操作成功')
        return redirect(url_for('approve_expense'))

    expenses = Expense.query.filter_by(status=STATUS_PENDING).all()
    return render_template('approve.html', expenses=expenses, role='finance')

# --------------------------------------------------
#                  编辑本人报销
# --------------------------------------------------
@app.route('/edit/<int:expense_id>', methods=['GET', 'POST'])
def edit_expense(expense_id):
    if 'user_id' not in session:
        flash('无权限')
        return redirect(url_for('login'))

    expense = Expense.query.get(expense_id)
    if not expense or expense.submitter_id != session['user_id']:
        flash('无权限')
        return redirect(url_for('view_records'))
    if expense.status == STATUS_APPROVED:
        flash('报销单已审批通过，无法编辑')
        return redirect(url_for('view_records'))

    types = ExpenseType.query.all()

    if request.method == 'POST':
        expense.date    = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        expense.type_id = int(request.form['type'])
        expense.title   = request.form['title']
        expense.amount  = Decimal(request.form['amount'])
        expense.description = request.form['description']

        invoice_file = request.files.get('invoice')
        new_name = save_invoice(invoice_file)
        if new_name:
            expense.invoice = new_name

        db.session.commit()
        flash('修改成功')
        return redirect(url_for('view_records'))

    return render_template('edit.html', expense=expense, types=types)

# --------------------------------------------------
#               全部记录（财务 & 老板）
# --------------------------------------------------
@app.route('/all_records')
def all_records():
    if 'user_id' not in session or session.get('role') not in ['finance', 'boss']:
        flash('无权限')
        return redirect(url_for('login'))

    role  = session.get('role')
    query = Expense.query.join(User, Expense.submitter_id == User.id)

    # ---------- 筛选 ----------
    start_date = request.args.get('start_date')
    end_date   = request.args.get('end_date')
    username   = request.args.get('username')
    status     = request.args.get('status')
    type_id    = request.args.get('type')

    if start_date:
        try:
            query = query.filter(Expense.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
        except ValueError:
            pass
    if end_date:
        try:
            query = query.filter(Expense.date <= datetime.strptime(end_date, '%Y-%m-%d').date())
        except ValueError:
            pass
    if username:
        query = query.filter(User.username == username)
    if status:
        query = query.filter(Expense.status == status)
    if type_id:
        query = query.filter(Expense.type_id == int(type_id))

    # ---------- 排序 ----------
    sort = request.args.get('sort')
    if sort == 'date_asc':
        query = query.order_by(Expense.date.asc())
    elif sort == 'date_desc':
        query = query.order_by(Expense.date.desc())
    elif sort == 'amount_asc':
        query = query.order_by(Expense.amount.asc())
    elif sort == 'amount_desc':
        query = query.order_by(Expense.amount.desc())

    expenses = query.all()
    total    = sum(e.amount for e in expenses)
    users    = User.query.all()
    types    = ExpenseType.query.all()
    return render_template('records.html', expenses=expenses, role=role,
                           total=total, users=users, types=types)

# --------------------------------------------------
#                发票预览（静态图片）
# --------------------------------------------------
@app.route('/invoice_preview/<filename>')
def invoice_preview(filename):
    return render_template('invoice_preview.html', invoice=filename)

# --------------------------------------------------
#             用户、类型等管理接口（略）
# --------------------------------------------------
@app.route('/user_manage')
def user_manage():
    if 'user_id' not in session or session.get('role') != 'finance':
        flash('无权限')
        return redirect(url_for('index'))
    users = User.query.all()
    return render_template('user_manage.html', users=users)

@app.route('/user_add', methods=['GET', 'POST'])
def user_add():
    if 'user_id' not in session or session.get('role') != 'finance':
        flash('无权限')
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        validate_username(username)

        realname = request.form['realname'].strip()
        password = request.form['password']
        role     = request.form['role']

        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
            return redirect(url_for('user_add'))

        user = User(username=username,
                    realname=realname,
                    password=generate_password_hash(password),
                    role=role)
        db.session.add(user)
        db.session.commit()
        flash('账号创建成功')
        return redirect(url_for('user_manage'))

    return render_template('user_add.html')

@app.route('/user_edit/<int:user_id>', methods=['GET', 'POST'])
def user_edit(user_id):
    if 'user_id' not in session or session.get('role') != 'finance':
        flash('无权限')
        return redirect(url_for('index'))

    user = User.query.get(user_id)
    if not user:
        flash('用户不存在')
        return redirect(url_for('user_manage'))

    if request.method == 'POST':
        user.role     = request.form['role']
        user.realname = request.form['realname'].strip()
        if request.form['password']:
            user.password = generate_password_hash(request.form['password'])
        db.session.commit()
        flash('账号信息已更新')
        return redirect(url_for('user_manage'))

    return render_template('user_edit.html', user=user)

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        flash('请先登录')
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        if not check_password_hash(user.password, old_password):
            flash('原密码错误')
            return redirect(url_for('change_password'))
        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash('密码修改成功')
        return redirect(url_for('index'))

    return render_template('change_password.html')

# ---------------- 类型管理 ----------------
@app.route('/type_manage', methods=['GET', 'POST'])
def type_manage():
    if 'user_id' not in session or session.get('role') != 'finance':
        flash('无权限')
        return redirect(url_for('index'))

    if request.method == 'POST':
        new_type = request.form.get('new_type').strip()
        if new_type and not ExpenseType.query.filter_by(name=new_type).first():
            db.session.add(ExpenseType(name=new_type))
            db.session.commit()
            flash('类型添加成功')
        else:
            flash('类型已存在或无效')

    types = ExpenseType.query.all()
    return render_template('type_manage.html', types=types)

@app.route('/type_edit/<int:type_id>', methods=['POST'])
def type_edit(type_id):
    if 'user_id' not in session or session.get('role') != 'finance':
        flash('无权限')
        return redirect(url_for('index'))

    t = ExpenseType.query.get(type_id)
    new_name = request.form.get('edit_name').strip()
    if t and new_name and not ExpenseType.query.filter_by(name=new_name).first():
        t.name = new_name
        db.session.commit()
        flash('类型已更新')
    else:
        flash('类型名无效或已存在')
    return redirect(url_for('type_manage'))

@app.route('/type_delete/<int:type_id>', methods=['POST'])
def type_delete(type_id):
    if 'user_id' not in session or session.get('role') != 'finance':
        flash('无权限')
        return redirect(url_for('index'))

    t = ExpenseType.query.get(type_id)
    if t:
        used = Expense.query.filter_by(type_id=t.id).first()
        if used:
            flash('该类型已被使用，无法删除')
        else:
            db.session.delete(t)
            db.session.commit()
            flash('类型已删除')
    return redirect(url_for('type_manage'))

# ---------------- 报销单删除 ----------------
@app.route('/expense_delete/<int:expense_id>', methods=['POST'])
def expense_delete(expense_id):
    if 'user_id' not in session or session.get('role') != 'finance':
        flash('无权限')
        return redirect(url_for('index'))

    expense = Expense.query.get(expense_id)
    if not expense:
        flash('报销单不存在')
        return redirect(request.referrer or url_for('all_records'))

    db.session.delete(expense)
    db.session.commit()
    flash('报销单已删除')
    return redirect(request.referrer or url_for('all_records'))

# --------------------------------------------------
#              初始化财务 / 老板账号
# --------------------------------------------------
def init_admin_users():
    created = False
    if not User.query.filter_by(username='finance').first():
        db.session.add(User(username='finance',
                            realname='财务',
                            password=generate_password_hash('123456'),
                            role='finance'))
        created = True
    if not User.query.filter_by(username='boss').first():
        db.session.add(User(username='boss',
                            realname='老板',
                            password=generate_password_hash('123456'),
                            role='boss'))
        created = True
    if created:
        db.session.commit()
        print("初始化财务 / 老板账号完毕（默认密码 123456）")

# --------------------------------------------------
@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    import shutil
    from docxtpl import DocxTemplate
    from PyPDF2 import PdfReader
    import subprocess
    import tempfile
    from datetime import datetime
    import os
    from pdf2image import convert_from_path
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from PyPDF2 import PdfWriter
    from io import BytesIO

    if 'user_id' not in session:
        flash('请先登录')
        return redirect(url_for('login'))
    user_id = session['user_id']
    user = User.query.get(user_id)
    if not user:
        flash('用户不存在')
        return redirect(url_for('view_records'))
    selected_ids = request.form.get('selected_ids', '')
    if not selected_ids:
        flash('请选择要生成报销单的记录')
        return redirect(url_for('view_records'))
    try:
        id_list = [int(i) for i in selected_ids.split(',') if i.strip().isdigit()]
    except Exception:
        flash('参数错误')
        return redirect(url_for('view_records'))
    if not id_list:
        flash('请选择要生成报销单的记录')
        return redirect(url_for('view_records'))
    expenses = Expense.query.filter(Expense.id.in_(id_list), Expense.submitter_id==user_id, Expense.status=='通过审批').order_by(Expense.date.asc()).all()
    if not expenses or len(expenses) != len(id_list):
        flash('部分报销记录不存在或无权操作')
        return redirect(url_for('view_records'))
    pages = [expenses[i:i+5] for i in range(0, len(expenses), 5)]
    total_pages = len(pages)
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_files = []
        pdf_files = []
        template_path = os.path.join('word_templates', 'expense_a5_template.docx')
        for idx, group in enumerate(pages):
            details = []
            for e in group:
                details.append({
                    'desc': e.title,
                    'amount': f'{float(e.amount):.2f}',
                    'description': e.description or ''
                })
            while len(details) < 5:
                details.append({'desc': '', 'amount': '', 'description': ''})
            total_amount = sum(float(e.amount) for e in group)
            total_amount_upper = num_to_rmb_upper(total_amount)
            context = {
                'date': group[0].date.strftime('%Y年%m月%d日') if group else '',
                'page_count': str(total_pages),
                'details': details,
                'total': f'{total_amount:.2f}',
                'amount_upper': total_amount_upper,
                'name': user.realname
            }
            tpl = DocxTemplate(template_path)
            docx_path = os.path.join(tmpdir, f'baoxiao_{idx+1}.docx')
            tpl.render(context)
            tpl.save(docx_path)
            docx_files.append(docx_path)
        for docx_file in docx_files:
            cmd = [
                'soffice', '--headless', '--convert-to', 'pdf', '--outdir', tmpdir, docx_file
            ]
            subprocess.run(cmd, check=True)
            pdf_file = os.path.splitext(docx_file)[0] + '.pdf'
            pdf_files.append(pdf_file)
        # 拼版A4竖向PDF（pdf2image+reportlab）
        a4_width, a4_height = A4
        gap = 1 * mm
        a4_pdf_writer = PdfWriter()
        a4_pdf_bytes_list = []
        # 先将所有A5 PDF转为图片
        a5_images = []
        for pdf in pdf_files:
            images = convert_from_path(pdf, dpi=300, size=(int(a4_width), None))
            a5_images.append(images[0])  # 每个A5只有一页
        # 每2张A5拼成1页A4
        for i in range(0, len(a5_images), 2):
            packet = BytesIO()
            can = canvas.Canvas(packet, pagesize=A4)
            # 上半部分
            img1 = a5_images[i]
            img1_width, img1_height = img1.size
            scale1 = min(a4_width / img1_width, (a4_height - gap) / 2 / img1_height)
            draw_w1 = img1_width * scale1
            draw_h1 = img1_height * scale1
            can.drawInlineImage(img1, 0, a4_height/2 + gap/2, width=draw_w1, height=draw_h1)
            # 下半部分
            if i+1 < len(a5_images):
                img2 = a5_images[i+1]
                img2_width, img2_height = img2.size
                scale2 = min(a4_width / img2_width, (a4_height - gap) / 2 / img2_height)
                draw_w2 = img2_width * scale2
                draw_h2 = img2_height * scale2
                can.drawInlineImage(img2, 0, 0, width=draw_w2, height=draw_h2)
            can.save()
            packet.seek(0)
            # 转为PDF页
            pdf_page = PdfReader(packet).pages[0]
            a4_pdf_writer.add_page(pdf_page)
        # 生成文件名始终为'报销单_日期.pdf'，不包含用户名
        filename = f'报销单_{datetime.now().strftime("%Y%m%d")}.pdf'
        final_pdf_path = os.path.join(tmpdir, filename)
        with open(final_pdf_path, 'wb') as f:
            a4_pdf_writer.write(f)
        # 用 BytesIO 读入内存，避免文件占用
        import io
        with open(final_pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        pdf_io = io.BytesIO(pdf_bytes)
        pdf_io.seek(0)
        return send_file(pdf_io, as_attachment=True, download_name=filename, mimetype='application/pdf')

# --------------------------------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        init_admin_users()
    print("USE_SQLSERVER:", os.environ.get("USE_SQLSERVER"))
    print("SQLALCHEMY_DATABASE_URI:", os.environ.get("SQLALCHEMY_DATABASE_URI"))
    app.run(debug=True)
