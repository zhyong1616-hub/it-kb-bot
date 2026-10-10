# -*- coding: utf-8 -*-
"""
自升级模块：让机器人在企微里收到 `/update` 后，自己拉取最新版本并重启。

更新源（三选一，配哪个用哪个，优先级从高到低）：
  1. 网址：KB_UPDATE_URL   —— 指到一个 .zip（也可指到含有 version.txt 的目录）
  2. 本地目录：KB_UPDATE_DIR 或 update_dir.txt —— 取目录里最新的 .zip
  3. Git：KB_UPDATE_GIT    —— git pull 到 KB_UPDATE_GIT_BRANCH

安全原则（与 更新.bat 完全一致）：
  - 只覆盖**代码**（app/ tools/ tests/ *.bat *.md requirements.txt）
  - **绝不触碰** .env 和 data/（凭据、知识库内容、审批记录）
  - 覆盖前自动备份到 _backup/时间戳/
  - 升级后写一份 _update_result.json，供重启后回报结果

重启方式：两段式 —— 本模块只负责"准备 + 落盘 + 触发退出"，
真正的重启由 run_service.bat 的自动重启循环 / 计划任务完成。
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile

def _base():
    """服务目录：默认是 self_update.py 的上上级；测试可用 KB_SELF_DIR 指到临时目录，
    避免"升级测试"覆盖真实源码。"""
    return os.environ.get('KB_SELF_DIR') or \
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BASE = _base()          # 兼容旧引用（仅用于只读判断）


def version_file():
    return os.path.join(_base(), 'VERSION')


def result_file():
    return os.path.join(_base(), '_update_result.json')

# 代码类目录/文件才覆盖；其余一律不动
CODE_DIRS = ('app', 'tools', 'tests')
CODE_GLOBS = ('*.bat', '*.md', 'requirements.txt', 'install.sh')


# ---------- 版本 ----------

def local_version():
    try:
        with open(version_file(), encoding='utf-8') as f:
            return f.read().strip() or '0.0.0'
    except OSError:
        return '0.0.0'


def _parse_ver(v):
    """把 '1.2.3' 之类转成可比较的元组；非数字部分忽略。"""
    out = []
    for part in str(v).split('.'):
        num = ''
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:4])


def ver_newer(a, b):
    """a 是否比 b 新"""
    return _parse_ver(a) > _parse_ver(b)


# ---------- 配置（懒加载，避免 .env 加载顺序坑） ----------

def _conf():
    return {
        'url': os.environ.get('KB_UPDATE_URL', '').strip(),
        'dir': os.environ.get('KB_UPDATE_DIR', '').strip(),
        'git': os.environ.get('KB_UPDATE_GIT', '').strip(),
        'git_branch': os.environ.get('KB_UPDATE_GIT_BRANCH', '').strip(),
        # 自动拉取（零操作）：KB_AUTO_UPDATE=1 开启，间隔分钟数 KB_AUTO_UPDATE_INTERVAL
        'auto': os.environ.get('KB_AUTO_UPDATE', '0').strip() == '1',
        'auto_interval': int(os.environ.get('KB_AUTO_UPDATE_INTERVAL', '10') or 10),
    }


def auto_enabled():
    return _conf()['auto']


def auto_interval_sec():
    iv = _conf()['auto_interval']
    return max(60, iv * 60)      # 最少 1 分钟，防止手滑设成 0 刷爆



def _update_dir():
    """本地更新目录：env 优先，其次 update_dir.txt，最后内置默认。"""
    c = _conf()
    if c['dir']:
        return c['dir']
    txt = os.path.join(_base(), 'update_dir.txt')
    if os.path.exists(txt):
        for line in open(txt, encoding='utf-8', errors='ignore'):
            line = line.strip()
            if line and not line.startswith('#') and not line.upper().startswith('REM'):
                return line
    return r'F:\KIDE_BOT_update'


def source_ready():
    c = _conf()
    if c['url'] or c['git']:
        return True
    return bool(_update_dir())


def describe_source():
    c = _conf()
    if c['url']:
        return f'网址 {c["url"]}'
    if c['git']:
        return f'Git {c["git"]}' + (f'（分支 {c["git_branch"]}）' if c['git_branch'] else '')
    return f'本地目录 {_update_dir()}'


# ---------- 备份 ----------

def _stamp():
    return time.strftime('%Y%m%d-%H%M%S')


def backup_current():
    bak = os.path.join(_base(), '_backup', _stamp())
    os.makedirs(bak, exist_ok=True)
    app = os.path.join(_base(), 'app')
    if os.path.isdir(app):
        shutil.copytree(app, os.path.join(bak, 'app'), dirs_exist_ok=True)
    for d in ('tools', 'tests'):
        src = os.path.join(_base(), d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(bak, d), dirs_exist_ok=True)
    for name in os.listdir(_base()):
        if name.lower().endswith(('.bat', '.md', '.sh')) or name == 'requirements.txt':
            try:
                shutil.copy(os.path.join(_base(), name), os.path.join(bak, name))
            except OSError:
                pass
    return bak


# ---------- 覆盖 ----------

def apply_tree(src):
    """把 src 下的代码覆盖到服务目录，保留 .env 与 data/。返回写过的条目数。"""
    n = 0
    for d in CODE_DIRS:
        sp = os.path.join(src, d)
        if not os.path.isdir(sp):
            continue
        dp = os.path.join(_base(), d)
        os.makedirs(dp, exist_ok=True)
        for name in os.listdir(sp):
            s, t = os.path.join(sp, name), os.path.join(dp, name)
            if os.path.isdir(s):
                shutil.copytree(s, t, dirs_exist_ok=True)
            else:
                shutil.copy(s, t)
            n += 1
    for name in os.listdir(src):
        low = name.lower()
        if low.endswith(('.bat', '.md', '.sh')) or name == 'requirements.txt' or name == 'VERSION':
            s, t = os.path.join(src, name), os.path.join(_base(), name)
            try:
                shutil.copy(s, t)
                n += 1
            except OSError:
                pass
    _clean_pycache()
    return n


def _clean_pycache():
    for root, dirs, _ in os.walk(_base()):
        for d in list(dirs):
            if d == '__pycache__':
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)


# ---------- 取包 ----------

def _find_code_root(extract_dir):
    if os.path.isdir(os.path.join(extract_dir, 'app')):
        return extract_dir
    for name in os.listdir(extract_dir):
        d = os.path.join(extract_dir, name)
        if os.path.isdir(d) and os.path.isdir(os.path.join(d, 'app')):
            return d
    return None


def _extract_zip(path, workdir):
    with zipfile.ZipFile(path) as z:
        z.extractall(workdir)
    return _find_code_root(workdir)


def fetch_from_url(url, workdir):
    """下载 .zip 并解压，返回 (代码根, 远端版本, 错误)"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'IT-KB-Bot/1.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(64 * 1024 * 1024)
    except Exception as e:
        return None, '', f'下载失败：{e}'
    low = url.split('?')[0].lower()
    if low.endswith('.zip') or data[:2] == b'PK':
        zp = os.path.join(workdir, 'pkg.zip')
        with open(zp, 'wb') as f:
            f.write(data)
        root = _extract_zip(zp, workdir)
        if not root:
            return None, '', '包结构不对（没有 app/ 目录）'
        return root, _read_ver_from(root), ''
    # 不是 zip：当成 version.txt
    try:
        return None, data.decode('utf-8', 'ignore').strip(), '远端只给了版本号，未提供包'
    except Exception:
        return None, '', '无法识别远端内容'


def _read_ver_from(root):
    vf = os.path.join(root, 'VERSION')
    if os.path.exists(vf):
        try:
            return open(vf, encoding='utf-8').read().strip()
        except OSError:
            pass
    return ''


def fetch_from_dir(path, workdir):
    if not os.path.isdir(path):
        return None, '', f'更新目录不存在：{path}'
    zips = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith('.zip')]
    if not zips:
        return None, '', f'更新目录里没有 .zip：{path}'
    newest = max(zips, key=os.path.getmtime)
    root = _extract_zip(newest, workdir)
    if not root:
        return None, '', '包结构不对（没有 app/ 目录）'
    return root, _read_ver_from(root), ''


def fetch_from_git(repo, branch, workdir):
    try:
        cmd = ['git', 'clone', '--depth', '1']
        if branch:
            cmd += ['-b', branch]
        cmd += [repo, workdir]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return None, '', '本机没装 git，无法用 Git 更新源'
    except subprocess.TimeoutExpired:
        return None, '', 'git clone 超时'
    if r.returncode != 0:
        return None, '', f'git clone 失败：{(r.stderr or "")[:200]}'
    root = _find_code_root(workdir) or workdir
    return root, _read_ver_from(root), ''


def git_remote_version(repo, branch=''):
    """轻量：只问远程最新 commit 的 VERSION，不下载整个仓库。

    用浅克隆到临时目录再读 VERSION（--depth 1 很快，几秒）。
    返回 (版本号, 错误)。版本号为空表示读不到。
    """
    tmp = tempfile.mkdtemp(prefix='kb_ver_')
    try:
        cmd = ['git', 'clone', '--depth', '1', '--filter=blob:none', '--sparse']
        if branch:
            cmd += ['-b', branch]
        cmd += [repo, tmp]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            # 退回普通浅克隆
            cmd2 = ['git', 'clone', '--depth', '1']
            if branch:
                cmd2 += ['-b', branch]
            cmd2 += [repo, tmp]
            r = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return '', f'git 探测失败：{(r.stderr or "")[:150]}'
        root = _find_code_root(tmp) or tmp
        return _read_ver_from(root), ''
    except FileNotFoundError:
        return '', '本机没装 git'
    except subprocess.TimeoutExpired:
        return '', 'git 探测超时'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 对外主入口 ----------

def quick_version():
    """只探测"源上的版本号"，不下载整个包（定时检查用，省流量）。

    返回 (远端版本, 错误)；远端版本为空表示读不到（会保守地当作"需完整检查"）。
    """
    c = _conf()
    if c['git']:
        return git_remote_version(c['git'], c['git_branch'])
    if c['url']:
        # 若 URL 指向 .zip，尝试同目录的 VERSION 文件
        base = c['url'].rsplit('/', 1)[0]
        try:
            req = urllib.request.Request(base + '/VERSION',
                                         headers={'User-Agent': 'IT-KB-Bot/1.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read(64).decode('utf-8', 'ignore').strip(), ''
        except Exception:
            return '', ''      # 拿不到就交给完整检查
    if c['dir'] or _update_dir():
        try:
            zips = [os.path.join(_update_dir(), f) for f in os.listdir(_update_dir())
                    if f.lower().endswith('.zip')]
        except OSError:
            return '', ''
        if not zips:
            return '', ''
        newest = max(zips, key=os.path.getmtime)
        tmp = tempfile.mkdtemp(prefix='kb_ver_')
        try:
            with zipfile.ZipFile(newest) as z:
                for name in z.namelist():
                    if name.endswith('VERSION'):
                        return z.read(name).decode('utf-8', 'ignore').strip(), ''
        except Exception:
            pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return '', ''


def check():
    """只检查，不落地。返回 dict(ok, local, remote, newer, msg, source, root, workdir)"""
    workdir = tempfile.mkdtemp(prefix='kb_upd_')
    lv = local_version()
    c = _conf()
    try:
        if c['url']:
            root, rv, err = fetch_from_url(c['url'], workdir)
        elif c['git']:
            root, rv, err = fetch_from_git(c['git'], c['git_branch'], workdir)
        else:
            root, rv, err = fetch_from_dir(_update_dir(), workdir)
    except Exception as e:
        return {'ok': False, 'local': lv, 'remote': '', 'newer': False,
                'msg': f'检查失败：{e}', 'source': describe_source(),
                'root': None, 'workdir': workdir}
    if err:
        return {'ok': False, 'local': lv, 'remote': rv, 'newer': False,
                'msg': err, 'source': describe_source(), 'root': None, 'workdir': workdir}
    newer = ver_newer(rv, lv) if rv else True   # 无版本号则默认尝试更新
    return {'ok': True, 'local': lv, 'remote': rv or '(未标注)', 'newer': newer,
            'msg': '', 'source': describe_source(), 'root': root, 'workdir': workdir}


def do_update(plan):
    """执行更新。plan 来自 check()。返回 (是否成功, 文本)。"""
    if not plan.get('ok'):
        return False, plan.get('msg') or '检查失败'
    root = plan.get('root')
    if not root or not os.path.isdir(os.path.join(root, 'app')):
        return False, '拿到的包不完整（没有 app/ 目录），已中止，未改动任何文件。'
    try:
        bak = backup_current()
        n = apply_tree(root)
        # 记录结果，供重启后回报
        json.dump({'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'from': plan.get('local'), 'to': plan.get('remote'),
                   'backup': bak, 'files': n, 'source': plan.get('source')},
                  open(result_file(), 'w', encoding='utf-8'), ensure_ascii=False)
        return True, (f'已覆盖 {n} 个条目（备份：{os.path.basename(bak)}）')
    except Exception as e:
        return False, f'应用更新失败：{e}'
    finally:
        shutil.rmtree(plan.get('workdir') or '', ignore_errors=True)


def pop_result():
    """取出上次更新的结果（取完即删），供重启后回报。"""
    if not os.path.exists(result_file()):
        return None
    try:
        data = json.load(open(result_file(), encoding='utf-8'))
    except Exception:
        data = None
    try:
        os.remove(result_file())
    except OSError:
        pass
    return data


# ---------- 定时自动升级（零操作） ----------

_last_probe = {'ts': 0.0, 'err_streak': 0}


def _log(msg):
    print(f'[自动升级] {msg}')


def probe_once():
    """做一次"探测 + 必要时完整拉取"。

    返回 dict:
      {'action': 'none'|'update', 'reason':..., 'plan':...}
    - 先 quick_version() 轻量比版本
    - 版本没变 -> 直接 none（不下载）
    - 版本变新 或 探测不到 -> 走完整 check()，真的新才 update
    """
    lv = local_version()
    try:
        rv, err = quick_version()
    except Exception as e:
        rv, err = '', str(e)

    if rv and not ver_newer(rv, lv):
        _last_probe['err_streak'] = 0
        return {'action': 'none', 'reason': f'已是最新（{lv}）',
                'local': lv, 'remote': rv, 'plan': None}

    # 探测不到版本号，或确实更新 -> 完整检查
    plan = check()
    if not plan.get('ok'):
        _last_probe['err_streak'] += 1
        # 失败静默重试：只在前几次打印，避免刷屏
        if _last_probe['err_streak'] <= 3:
            _log(f'检查失败（第 {_last_probe["err_streak"]} 次，将静默重试）：{plan.get("msg")}')
        return {'action': 'none', 'reason': plan.get('msg') or '检查失败',
                'local': lv, 'remote': '', 'plan': None}
    _last_probe['err_streak'] = 0
    if not plan.get('newer'):
        return {'action': 'none', 'reason': f'已是最新（{lv}）',
                'local': lv, 'remote': plan.get('remote', ''), 'plan': None}
    return {'action': 'update', 'reason': f'{lv} -> {plan["remote"]}',
            'local': lv, 'remote': plan.get('remote', ''), 'plan': plan}


async def auto_loop(on_update_ready):
    """后台协程：每隔 auto_interval 探测一次，有新版就回调 on_update_ready(plan)。

    on_update_ready 由 main.py 提供（负责回复、落盘、退出重启）。
    本函数自身永不抛异常，保证后台任务稳定。
    """
    import asyncio
    # 启动后先等一会儿，别和启动流程抢资源
    await asyncio.sleep(30)
    while True:
        try:
            if auto_enabled() and source_ready():
                res = probe_once()
                if res['action'] == 'update':
                    _log(f'发现新版本 {res["reason"]}，准备自动升级')
                    await on_update_ready(res['plan'])
        except Exception as e:
            _log(f'循环异常（忽略并继续）：{e}')
        try:
            await asyncio.sleep(auto_interval_sec())
        except Exception:
            await asyncio.sleep(300)
