        fields.append("password_hash=?");vals.append(generate_password_hash(d["password"]))
    if d.get("role") in ("admin","user"):fields.append("role=?");vals.append(d["role"])
    if fields:
        vals.append(uid);con.execute("UPDATE users SET "+",".join(fields)+" WHERE id=?",vals);con.commit()
    con.close();return jsonify(ok=True)

@app.route("/api/stats")
def stats():
    if not me():return jsonify(ok=False),401
    con=db();scope="" if is_admin() else " WHERE username=?";args=() if is_admin() else (me(),)
    total=con.execute("SELECT COUNT(*) n FROM links"+scope,args).fetchone()["n"];today=datetime.now().strftime("%Y-%m-%d")
    if is_admin():
        today_add=con.execute("SELECT COUNT(*) n FROM links WHERE created_at LIKE ?",(today+"%",)).fetchone()["n"]
        users=list(con.execute("SELECT username,COUNT(*) count FROM links GROUP BY username ORDER BY count DESC,username").fetchall())
        withdrawn=con.execute("SELECT COUNT(*) n FROM activity WHERE action='撤回' AND created_at LIKE ?",(today+"%",)).fetchone()["n"]
    else:
        today_add=con.execute("SELECT COUNT(*) n FROM links WHERE username=? AND created_at LIKE ?",(me(),today+"%")).fetchone()["n"];users=[{"username":me(),"count":total}]
        withdrawn=con.execute("SELECT COUNT(*) n FROM activity WHERE action='撤回' AND username=? AND created_at LIKE ?",(me(),today+"%")).fetchone()["n"]
    user_count=con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] if is_admin() else 1
    if is_admin():
        per_user=list(con.execute("""SELECT u.username,u.role,
          COALESCE(SUM(CASE WHEN a.action='新增' THEN 1 ELSE 0 END),0) added,
          COALESCE(SUM(CASE WHEN a.action='撤回' THEN 1 ELSE 0 END),0) withdrawn,
          COALESCE(SUM(CASE WHEN a.action='重复' THEN 1 ELSE 0 END),0) duplicates
          FROM users u LEFT JOIN activity a ON a.username=u.username
          GROUP BY u.id,u.username,u.role ORDER BY u.id""").fetchall())
    else:
        r=con.execute("""SELECT ? username,? role,
          COALESCE(SUM(CASE WHEN action='新增' THEN 1 ELSE 0 END),0) added,
          COALESCE(SUM(CASE WHEN action='撤回' THEN 1 ELSE 0 END),0) withdrawn,
          COALESCE(SUM(CASE WHEN action='重复' THEN 1 ELSE 0 END),0) duplicates
          FROM activity WHERE username=?""",(me(),session.get("role"),me())).fetchone();per_user=[r]
    con.close();return jsonify(total=total,today=today_add,withdrawn=withdrawn,user_count=user_count,users=users,per_user=per_user)

@app.route("/api/check",methods=["POST"])
def check_add():
    if not me():return jsonify(ok=False,message="请先登录"),401
    d=request.get_json(silent=True) or request.form;url=normalize_url(d.get("url"));user=(d.get("user") or me()).strip() if is_admin() else me()
    if not url:return jsonify(ok=False,message="请输入链接"),400
    if not is_valid_url(url):return jsonify(ok=False,message="请输入有效的网址链接"),400
    con=db();ex=con.execute("SELECT username,created_at FROM links WHERE url=?",(url,)).fetchone()
    if ex:
        log(con,"重复",user,url);con.commit();con.close();return jsonify(ok=True,exists=True,message=f'已存在（用户：{ex["username"]}）')
    con.execute("INSERT INTO links(username,url,created_at) VALUES(?,?,?)",(user,url,now()));log(con,"新增",user,url);con.commit();con.close()
    return jsonify(ok=True,exists=False,message="不存在，已自动新增")

@app.route("/api/import",methods=["POST"])
def import_file():
    if not me():return jsonify(ok=False,message="请先登录"),401
    f=request.files.get("file");user=(request.form.get("user") or me()).strip() if is_admin() else me()
    if not f:return jsonify(ok=False,message="请选择文件"),400
    data=f.read();raw=None
    for enc in ("utf-8-sig","utf-16","gb18030","big5"):
        try:raw=data.decode(enc);break
        except UnicodeDecodeError:pass
    if raw is None:raw=data.decode("utf-8",errors="ignore")
    candidates=[]
    if (f.filename or "").lower().endswith(".csv"):
        for row in csv.reader(io.StringIO(raw)):
            for cell in row:
                found=extract_urls(cell);candidates.extend(found)
                if not found and is_valid_url(cell.strip()):candidates.append(cell.strip())
    else:
        candidates=extract_urls(raw)
        for line in raw.splitlines():
            line=line.strip().strip("\"'")
            if line and not extract_urls(line) and is_valid_url(line):candidates.append(line)
    seen=set();urls=[];invalid=0
    for item in candidates:
        u=normalize_url(item)
        if not is_valid_url(u):invalid+=1;continue
        if u not in seen:seen.add(u);urls.append(u)
    if not urls:return jsonify(ok=True,added=0,duplicates=0,invalid=invalid,total=0)
    con=db()
    try:
        ts=now()
        inserted=con.execute("""INSERT INTO links(username,url,created_at)
          SELECT ?,u,? FROM unnest(?::text[]) AS u
          ON CONFLICT (url) DO NOTHING RETURNING url""",(user,ts,urls)).fetchall()
        inserted_urls={r["url"] for r in inserted};added=len(inserted_urls);dupes=len(urls)-added
        actions=[("新增" if u in inserted_urls else "重复",user,u,ts) for u in urls]
        con.executemany("INSERT INTO activity(action,username,url,created_at) VALUES(?,?,?,?)",actions)
        con.commit()
    except Exception:
        con.rollback();raise
    finally:con.close()
    return jsonify(ok=True,added=added,duplicates=dupes,invalid=invalid,total=len(urls))

@app.route("/api/links")
def links():
    if not me():return jsonify(ok=False),401
    user=(request.args.get("user") or me()).strip();user=user if is_admin() else me();limit=min(int(request.args.get("limit",300)),1000)
    con=db();rows=list(con.execute("SELECT * FROM links WHERE username=? ORDER BY id DESC LIMIT ?",(user,limit)).fetchall());count=con.execute("SELECT COUNT(*) n FROM links WHERE username=?",(user,)).fetchone()["n"];con.close()
    return jsonify(count=count,items=rows)
@app.route("/api/history")
def history():
    if not me():return jsonify(ok=False),401
    con=db();rows=list(con.execute("SELECT * FROM links ORDER BY id DESC LIMIT 100").fetchall()) if is_admin() else list(con.execute("SELECT * FROM links WHERE username=? ORDER BY id DESC LIMIT 100",(me(),)).fetchall());con.close()
    return jsonify(items=rows)
@app.route("/api/delete/<int:link_id>",methods=["DELETE"])
def delete(link_id):
    if not me():return jsonify(ok=False),401
    con=db();row=con.execute("SELECT * FROM links WHERE id=?",(link_id,)).fetchone()
    if not row:con.close();return jsonify(ok=False,message="记录不存在"),404
    if not is_admin() and row["username"]!=me():con.close();return jsonify(ok=False,message="无权限"),403
    con.execute("DELETE FROM links WHERE id=?",(link_id,));log(con,"撤回",row["username"],row["url"]);con.commit();con.close();return jsonify(ok=True)
@app.route("/export.csv")
def export_csv():
    if not me():return redirect("/login")
    con=db();rows=list(con.execute("SELECT username,url,created_at FROM links ORDER BY id DESC").fetchall()) if is_admin() else list(con.execute("SELECT username,url,created_at FROM links WHERE username=? ORDER BY id DESC",(me(),)).fetchall());con.close()
    out=io.StringIO();w=csv.writer(out);w.writerow(["用户","链接","新增时间"])
    for r in rows:w.writerow([r["username"],r["url"],r["created_at"]])
    data=io.BytesIO(("\ufeff"+out.getvalue()).encode());data.seek(0)
    return send_file(data,mimetype="text/csv",as_attachment=True,download_name="links.csv")
init_db()
@app.route("/health")
def health():return jsonify(ok=True)
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8080")),debug=False)
