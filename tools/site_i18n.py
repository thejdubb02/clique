#!/usr/bin/env python3
"""Build the public site from one copy table plus facts from the repo.

English lives in PAGES['en']. The other languages must have the same keys.
CLI names, the grok config example, and the Python version are read from
clis.toml / pyproject.toml at generate time, so the site cannot advertise a
CLI the catalogue dropped or a Python floor the package no longer claims.

`python3 tools/site_i18n.py` writes site/<lang>/index.html (English at
site/index.html) and site/i18n.lock.json.

`python3 tools/site_i18n.py --check` is what CI runs. It fails when:

- generated HTML does not match what is committed
- a translation is missing a key
- English changed and that language's string did not
- a featured CLI id is not in clis.toml

It does not machine-translate. New English still needs a person (or the
other window) to write the other languages; the check is what makes that
unskippable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "site"
LOCK = ROOT / "i18n.lock.json"

STAR = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.75.75 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25"/></svg>'
OCTO = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8"/></svg>'
CLONE = "git clone https://github.com/thejdubb02/clique.git\ncd clique\npython3 -m clique password\npython3 -m clique"
CSS_V = "11"

#: Homepage strip. Editorial order; every id must exist in clis.toml.
FEATURED = ("claude", "grok", "gemini", "codex", "opencode", "cursor", "cline", "shell")
SHORT_LEDE = 4
EXAMPLE_CLI = "grok"
META_KEYS = {"html_lang", "locale"}

HREFLANG = """\
<link rel="alternate" hreflang="en" href="https://useclique.dev/">
<link rel="alternate" hreflang="zh-Hans" href="https://useclique.dev/zh/">
<link rel="alternate" hreflang="ja" href="https://useclique.dev/ja/">
<link rel="alternate" hreflang="ko" href="https://useclique.dev/ko/">
<link rel="alternate" hreflang="pt-BR" href="https://useclique.dev/pt-br/">
<link rel="alternate" hreflang="de" href="https://useclique.dev/de/">
<link rel="alternate" hreflang="x-default" href="https://useclique.dev/">"""

LANGS = [
    ("/", "en", "English"),
    ("/zh/", "zh-Hans", "简体中文"),
    ("/ja/", "ja", "日本語"),
    ("/ko/", "ko", "한국어"),
    ("/pt-br/", "pt-BR", "Português"),
    ("/de/", "de", "Deutsch"),
]


def langs_html(current: str) -> str:
    bits = []
    for href, code, label in LANGS:
        on = ' class="on"' if code == current else ""
        bits.append(f'<a href="{href}"{on} lang="{code}">{label}</a>')
    return " · ".join(bits)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _toml_value(value) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(value)


def load_clis() -> dict:
    data = tomllib.loads((REPO / "clique" / "config" / "clis.toml").read_text())
    return data.get("cli") or {}


def python_floor() -> str:
    text = (REPO / "pyproject.toml").read_text()
    match = re.search(r'requires-python\s*=\s*">=([^"]+)"', text)
    if not match:
        raise SystemExit("pyproject.toml has no requires-python floor")
    return match.group(1)


def short_label(label: str) -> str:
    return label[:-4] if label.endswith(" CLI") else label


def facts() -> dict:
    clis = load_clis()
    missing = [cid for cid in FEATURED if cid not in clis]
    if missing:
        raise SystemExit(
            "featured CLI id not in clis.toml: " + ", ".join(missing)
            + "\nThe homepage strip is FEATURED in tools/site_i18n.py. "
            "Remove it there, or put the CLI back in the catalogue."
        )
    names = [short_label(clis[cid]["label"]) for cid in FEATURED]
    example = clis[EXAMPLE_CLI]
    snippet = [f"[cli.{EXAMPLE_CLI}]"]
    for key in ("label", "command", "args", "color"):
        snippet.append(f"{key:<7} = {_toml_value(example[key])}")
    version = python_floor()
    return {
        "cli_strip": " · ".join(names),
        "cli_short": ", ".join(names[:SHORT_LEDE]),
        "python": f"{version}+",
        "python_badge": f"{version}%2B",
        "cfg_example": "\n".join(snippet),
    }


def fill(text: str, values: dict) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def copy_keys() -> list[str]:
    return sorted(k for k in PAGES["en"] if k not in META_KEYS)


def page_path(slug: str) -> Path:
    return ROOT / "index.html" if slug == "en" else ROOT / slug / "index.html"


def render(slug: str, values: dict) -> str:
    t = dict(PAGES[slug])
    for key in copy_keys():
        t[key] = fill(t[key], values)
    prefix = "" if slug == "en" else "../"
    canonical = "https://useclique.dev/" if slug == "en" else f"https://useclique.dev/{slug}/"
    word = "/" if slug == "en" else f"/{slug}/"
    return TMPL.format(
        slug=slug,
        prefix=prefix,
        canonical=canonical,
        word_href=word,
        hreflang=HREFLANG,
        star=STAR,
        octo=OCTO,
        clone=CLONE,
        clis=values["cli_strip"],
        python_badge=values["python_badge"],
        cfg_example=values["cfg_example"],
        css_v=CSS_V,
        langs=langs_html(t["html_lang"]),
        **t,
    )


def current_lock() -> dict:
    return {
        key: {lang: _sha(PAGES[lang][key]) for lang in PAGES}
        for key in copy_keys()
    }


def translation_problems() -> list[str]:
    problems = []
    required = set(copy_keys()) | META_KEYS
    for lang, table in PAGES.items():
        missing = sorted(required - set(table))
        extra = sorted(set(table) - required)
        if missing:
            problems.append(f"{lang}: missing {', '.join(missing)}")
        if extra:
            problems.append(f"{lang}: extra {', '.join(extra)}")
    if not LOCK.exists():
        problems.append(f"missing {LOCK.relative_to(REPO)} — run python3 tools/site_i18n.py")
        return problems
    previous = json.loads(LOCK.read_text())
    for key in copy_keys():
        was = previous.get(key) or {}
        now_en = _sha(PAGES["en"][key])
        if not was:
            problems.append(f"{key}: new English string, translate it in every language then regenerate")
            continue
        if was.get("en") == now_en:
            continue
        for lang in PAGES:
            if lang == "en":
                continue
            if _sha(PAGES[lang][key]) == was.get(lang):
                problems.append(
                    f"{lang}.{key}: English changed and this translation did not"
                )
    return problems


PAGES = {
    "en": dict(
        html_lang="en",
        locale="en_US",
        title="CLIque: a folder for every CLI on the box",
        desc="Coding sessions in folders, in a browser, kept alive in tmux. Self-host is free. Paid is for people who don't want to run it themselves.",
        ogdesc="A browser panel for every coding CLI on the box. Self-host is free. Paid later if you don't want to run it yourself.",
        kicker="self-hosted · MIT · 24 MB · no telemetry",
        h1='A <em>folder</em> for every CLI on the box.',
        lede="Coding sessions in folders, in a browser, kept alive in tmux. {cli_short}, or four lines of config for the next one. Self-host is free. Paid is for people who don't want to run it themselves.",
        tag="Folders, tmux, a browser.",
        copy="Copy",
        copied="Copied",
        then="Python {python} and tmux. Nothing else. Then open <code>http://127.0.0.1:3200</code>.",
        self_b="Self-host",
        self="Free forever. MIT. Clone it, it runs on your box, it binds loopback.",
        paid_b="Don't want to self-host",
        paid="Paid comes later. Sessions stay on a machine you control. We don't host a shell for you.",
        f24_b="24 MB resident.",
        f24="No framework, no <code>node_modules</code>, no build.",
        flop_b="Loopback on purpose.",
        flop="Anyone who reaches the panel has a terminal as you.",
        fring_b="The ring is the status.",
        fring="Spinning = working. Pulsing = waiting on you. Nothing = idle.",
        cfg_h="Adding a CLI is config, never code.",
        cfg_then="Reload the page. If adding one ever needs a code change, the design has failed.",
        faq_h="Questions",
        q1="Do I have to put it on the internet?",
        a1="No. It binds loopback. Tunnel it if you need another machine. Don't hang it on the public internet. Anyone who reaches the panel has a terminal as you.",
        q2="What does it cost?",
        a2="Self-host is free, MIT. We'll charge later for the version you don't set up yourself. The panel still runs on your machine.",
        q3="What do I need?",
        a3="Python {python} and tmux. Nothing else. No Node, no Docker, no account.",
        q4="How do I add another coding CLI?",
        a4="Four lines in config. Reload the page. If it ever needs a code change, the design has failed.",
        get_h="Clone it",
        coffee='<a href="https://buymeacoffee.com/jdubb">Buy me a coffee</a> if it saved you an afternoon.',
        search="Search sessions…",
        fold_work="Work",
        fold_home="Home",
        s1="auth rewrite",
        s2="landing page",
        s3="migrations",
        s4="weekly notes",
        waiting="waiting on you",
        term1="I'll stop here. The session store looks right;",
        term2="the middleware still lets a stale cookie through.",
        term3='Want me to patch <span class="file">app/auth.py</span>, or talk it through first?',
        prompt="Type a prompt, or a shell command…",
        run="Run",
        shot="CLIque: folders of CLI sessions on the left, a terminal on the right. A ring around each logo shows working, waiting, or idle.",
    ),
    "zh": dict(
        html_lang="zh-Hans",
        locale="zh_CN",
        title="CLIque: 给机器上每一套 CLI 一个文件夹",
        desc="编程会话按文件夹排好，开在浏览器里，靠 tmux 一直活着。自托管免费。付费是给不想自己搭的人。",
        ogdesc="浏览器里的编程会话面板。自托管免费。不想自己跑的话，以后再收费。",
        kicker="自托管 · MIT · 24 MB · 无遥测",
        h1="给机器上每一套 CLI 一个<em>文件夹</em>。",
        lede="编程会话按文件夹排好，开在浏览器里，靠 tmux 一直活着。{cli_short}，或四行配置加上去的下一个。自托管免费。付费是给不想自己搭的人。",
        tag="文件夹、tmux、浏览器。",
        copy="复制",
        copied="已复制",
        then="需要 Python {python} 和 tmux。没了。然后打开 <code>http://127.0.0.1:3200</code>。",
        self_b="自托管",
        self="永远免费。MIT。克隆下来，跑在你的机器上，只绑回环地址。",
        paid_b="不想自托管",
        paid="付费稍后。会话仍留在你控制的机器上。我们不会替你托管一个 shell。",
        f24_b="常驻 24 MB。",
        f24="没有框架，没有 <code>node_modules</code>，没有构建步骤。",
        flop_b="故意绑回环。",
        flop="谁能摸到面板，谁就有你的终端。",
        fring_b="环就是状态。",
        fring="转 = 在干活。闪 = 在等你。没有环 = 空闲。",
        cfg_h="加一套 CLI 是配置，永远不是改代码。",
        cfg_then="刷新页面。如果加一套还得改代码，设计就失败了。",
        faq_h="常见问题",
        q1="一定要挂到公网吗？",
        a1="不用。默认只绑回环地址。要从别的机器连，自己加隧道。别直接挂公网。谁能摸到面板，谁就有你的终端。",
        q2="要花钱吗？",
        a2="自托管免费，MIT。稍后会收不想自己搭的那一版。面板仍跑在你的机器上。",
        q3="需要什么？",
        a3="Python {python} 和 tmux。没了。不要 Node，不要 Docker，不要账号。",
        q4="怎么再加一套编程 CLI？",
        a4="配置里四行。刷新页面。如果还得改代码，设计就失败了。",
        get_h="克隆下来",
        coffee='<a href="https://buymeacoffee.com/jdubb">请我喝杯咖啡</a>，如果它替你省了一个下午。',
        search="搜索会话…",
        fold_work="工作",
        fold_home="个人",
        s1="登录改写",
        s2="落地页",
        s3="迁移",
        s4="每周笔记",
        waiting="在等你",
        term1="先停在这里。会话存储看起来没问题；",
        term2="中间件还是会放过过期的 cookie。",
        term3='要我改 <span class="file">app/auth.py</span>，还是先一起看一眼？',
        prompt="输入提示词，或一条 shell 命令…",
        run="运行",
        shot="CLIque：左边是按文件夹排好的 CLI 会话，右边是终端。标志周围的环表示工作中、在等你、或空闲。",
    ),
    "ja": dict(
        html_lang="ja",
        locale="ja_JP",
        title="CLIque: マシン上のすべての CLI にフォルダを",
        desc="コーディングセッションをフォルダに分け、ブラウザで開き、tmux で生かし続ける。セルフホストは無料。有料は自分で立てたくない人向け。",
        ogdesc="ブラウザのコーディングセッションパネル。セルフホストは無料。自分で回したくなければ、有料は後から。",
        kicker="セルフホスト · MIT · 24 MB · テレメトリなし",
        h1='マシン上のすべての CLI に<em>フォルダ</em>を。',
        lede="コーディングセッションをフォルダに分け、ブラウザで開き、tmux で生かし続ける。{cli_short}、または設定4行で足した CLI。セルフホストは無料。有料は自分で立てたくない人向け。",
        tag="フォルダ、tmux、ブラウザ。",
        copy="コピー",
        copied="コピーしました",
        then="Python {python} と tmux。それだけ。あとは <code>http://127.0.0.1:3200</code> を開きます。",
        self_b="セルフホスト",
        self="永久無料。MIT。クローンして、自分のマシンで動かす。ループバックにだけバインドします。",
        paid_b="セルフホストしたくない",
        paid="有料は後から。セッションはあなたのマシンに残ります。こちらでシェルをホストすることはありません。",
        f24_b="常駐 24 MB。",
        f24="フレームワークなし、<code>node_modules</code> なし、ビルドなし。",
        flop_b="わざとループバック。",
        flop="パネルに届く人は、あなたの端末を持っています。",
        fring_b="リングが状態。",
        fring="回転＝作業中。点滅＝あなた待ち。なし＝アイドル。",
        cfg_h="CLI の追加は設定であり、コードではない。",
        cfg_then="ページを再読み込み。追加にコード変更が要るなら、設計は失敗しています。",
        faq_h="よくある質問",
        q1="インターネットに出す必要はありますか？",
        a1="いいえ。デフォルトはループバックだけです。別のマシンから使うならトンネルを。公開ネットに直接出さないでください。パネルに届く人は、あなたの端末を持っています。",
        q2="料金は？",
        a2="セルフホストは無料、MIT。自分で立てない版は後から有料になります。パネルはあなたのマシンで動きます。",
        q3="必要なものは？",
        a3="Python {python} と tmux。それだけ。Node も Docker もアカウントも不要です。",
        q4="コーディング CLI を足すには？",
        a4="設定に4行。ページを再読み込み。コード変更が要るなら、設計は失敗しています。",
        get_h="クローンする",
        coffee='午後をひとつ節約できたら、<a href="https://buymeacoffee.com/jdubb">コーヒーをおごってください</a>。',
        search="セッションを検索…",
        fold_work="仕事",
        fold_home="個人",
        s1="認証まわり",
        s2="ランディング",
        s3="マイグレーション",
        s4="週次メモ",
        waiting="あなた待ち",
        term1="ここで一旦止めます。セッション保存は問題なさそう。",
        term2="ミドルウェアはまだ期限切れ cookie を通します。",
        term3='<span class="file">app/auth.py</span> を直しますか。それとも先に一緒に見ますか。',
        prompt="プロンプトか、シェルコマンドを…",
        run="実行",
        shot="CLIque：左はフォルダ分けした CLI セッション、右はターミナル。ロゴのリングは作業中・待ち・アイドル。",
    ),
    "ko": dict(
        html_lang="ko",
        locale="ko_KR",
        title="CLIque: 머신 위 모든 CLI에 폴더를",
        desc="코딩 세션을 폴더로 나누고, 브라우저에서 열고, tmux로 살려 둡니다. 셀프호스트는 무료. 유료는 직접 돌리기 싫은 사람을 위한 겁니다.",
        ogdesc="브라우저 코딩 세션 패널. 셀프호스트는 무료. 직접 돌리기 싫다면 유료는 나중입니다.",
        kicker="셀프호스트 · MIT · 24 MB · 텔레메트리 없음",
        h1='머신 위 모든 CLI에 <em>폴더</em>를.',
        lede="코딩 세션을 폴더로 나누고, 브라우저에서 열고, tmux로 살려 둡니다. {cli_short}, 또는 설정 네 줄로 더한 CLI. 셀프호스트는 무료. 유료는 직접 돌리기 싫은 사람을 위한 겁니다.",
        tag="폴더, tmux, 브라우저.",
        copy="복사",
        copied="복사됨",
        then="Python {python}와 tmux. 그게 전부입니다. 그다음 <code>http://127.0.0.1:3200</code>을 여세요.",
        self_b="셀프호스트",
        self="영원히 무료. MIT. 클론해서 당신 머신에서 돌립니다. 루프백에만 바인드합니다.",
        paid_b="셀프호스트하기 싫다면",
        paid="유료는 나중에. 세션은 당신이 통제하는 머신에 남습니다. 셸을 대신 호스팅하지 않습니다.",
        f24_b="상주 24 MB.",
        f24="프레임워크 없음, <code>node_modules</code> 없음, 빌드 없음.",
        flop_b="일부러 루프백.",
        flop="패널에 닿는 사람은 당신의 터미널을 가집니다.",
        fring_b="링이 상태입니다.",
        fring="회전 = 작업 중. 깜빡임 = 당신 대기. 없음 = 유휴.",
        cfg_h="CLI를 더하는 일은 설정이지, 코드가 아닙니다.",
        cfg_then="페이지를 새로고침하세요. 추가에 코드 변경이 필요하면 설계가 실패한 겁니다.",
        faq_h="자주 묻는 질문",
        q1="인터넷에 올려야 하나요?",
        a1="아니요. 기본은 루프백만입니다. 다른 머신에서 쓰려면 터널을 쓰세요. 공인망에 그냥 걸지 마세요. 패널에 닿는 사람은 당신의 터미널을 가집니다.",
        q2="비용은요?",
        a2="셀프호스트는 무료, MIT. 직접 돌리지 않는 버전은 나중에 유료입니다. 패널은 당신 머신에서 돌아갑니다.",
        q3="뭐가 필요한가요?",
        a3="Python {python}와 tmux. 그게 전부입니다. Node도 Docker도 계정도 필요 없습니다.",
        q4="코딩 CLI를 더하려면?",
        a4="설정에 네 줄. 페이지를 새로고침. 코드 변경이 필요하면 설계가 실패한 겁니다.",
        get_h="클론하세요",
        coffee='오후 하나를 아껴 줬다면, <a href="https://buymeacoffee.com/jdubb">커피 한 잔</a>이요.',
        search="세션 검색…",
        fold_work="업무",
        fold_home="개인",
        s1="인증 고치기",
        s2="랜딩",
        s3="마이그레이션",
        s4="주간 노트",
        waiting="당신 대기",
        term1="여기까지. 세션 저장은 괜찮아 보입니다.",
        term2="미들웨어는 아직 만료된 쿠키를 통과시킵니다.",
        term3='<span class="file">app/auth.py</span>를 고칠까요, 아니면 먼저 같이 볼까요.',
        prompt="프롬프트나 셸 명령을 입력…",
        run="실행",
        shot="CLIque: 왼쪽은 폴더로 나눈 CLI 세션, 오른쪽은 터미널. 로고 링은 작업 중·대기·유휴.",
    ),
    "pt-br": dict(
        html_lang="pt-BR",
        locale="pt_BR",
        title="CLIque: uma pasta para cada CLI na máquina",
        desc="Sessões de código em pastas, no navegador, vivas no tmux. Self-host é grátis. Pago é para quem não quer rodar sozinho.",
        ogdesc="Painel no navegador para cada CLI na máquina. Self-host é grátis. Pago depois se você não quiser rodar sozinho.",
        kicker="self-hosted · MIT · 24 MB · sem telemetria",
        h1='Uma <em>pasta</em> para cada CLI na máquina.',
        lede="Sessões de código em pastas, no navegador, mantidas vivas no tmux. {cli_short}, ou qualquer CLI em quatro linhas. Self-host é grátis. Pago é para quem não quer rodar sozinho.",
        tag="Pastas, tmux, um navegador.",
        copy="Copiar",
        copied="Copiado",
        then="Python {python} e tmux. Só isso. Depois abra <code>http://127.0.0.1:3200</code>.",
        self_b="Self-host",
        self="Grátis para sempre. MIT. Clone, rode na sua máquina, só escuta em loopback.",
        paid_b="Não quer self-host",
        paid="Pago vem depois. As sessões ficam numa máquina que você controla. A gente não hospeda um shell para você.",
        f24_b="24 MB residentes.",
        f24="Sem framework, sem <code>node_modules</code>, sem build.",
        flop_b="Loopback de propósito.",
        flop="Quem alcança o painel tem um terminal como você.",
        fring_b="O anel é o status.",
        fring="Girando = trabalhando. Pulsando = esperando você. Nada = ocioso.",
        cfg_h="Acrescentar uma CLI é config, nunca código.",
        cfg_then="Recarregue a página. Se acrescentar uma exigir mudança de código, o desenho falhou.",
        faq_h="Perguntas",
        q1="Preciso colocar na internet?",
        a1="Não. Escuta só em loopback. Ponha um túnel se quiser de outra máquina. Não pendure na internet pública. Quem alcança o painel tem um terminal como você.",
        q2="Quanto custa?",
        a2="Self-host é grátis, MIT. Depois cobramos a versão que você não configura. O painel continua na sua máquina.",
        q3="O que eu preciso?",
        a3="Python {python} e tmux. Só isso. Sem Node, sem Docker, sem conta.",
        q4="Como acrescento outra CLI de código?",
        a4="Quatro linhas na config. Recarregue a página. Se precisar mudar código, o desenho falhou.",
        get_h="Clone",
        coffee='<a href="https://buymeacoffee.com/jdubb">Pague um café</a> se isso te poupou uma tarde.',
        search="Buscar sessões…",
        fold_work="Trabalho",
        fold_home="Pessoal",
        s1="auth rewrite",
        s2="landing",
        s3="migrações",
        s4="notas da semana",
        waiting="esperando você",
        term1="Paro aqui. O store da sessão parece certo;",
        term2="o middleware ainda deixa passar um cookie velho.",
        term3='Quer que eu corrija o <span class="file">app/auth.py</span>, ou olhamos juntos primeiro?',
        prompt="Digite um prompt, ou um comando de shell…",
        run="Run",
        shot="CLIque: pastas de sessões CLI à esquerda, um terminal à direita. O anel no logo mostra trabalhando, esperando ou ocioso.",
    ),
    "de": dict(
        html_lang="de",
        locale="de_DE",
        title="CLIque: ein Ordner für jede CLI auf der Box",
        desc="Coding-Sessions in Ordnern, im Browser, am Leben in tmux. Self-Host ist kostenlos. Bezahlt ist für Leute, die es nicht selbst betreiben wollen.",
        ogdesc="Ein Browser-Panel für jede CLI auf der Box. Self-Host ist kostenlos. Bezahlt später, wenn du es nicht selbst betreiben willst.",
        kicker="self-hosted · MIT · 24 MB · keine Telemetrie",
        h1='Ein <em>Ordner</em> für jede CLI auf der Box.',
        lede="Coding-Sessions in Ordnern, im Browser, am Leben gehalten in tmux. {cli_short} oder jede CLI in vier Zeilen. Self-Host ist kostenlos. Bezahlt ist für Leute, die es nicht selbst betreiben wollen.",
        tag="Ordner, tmux, ein Browser.",
        copy="Kopieren",
        copied="Kopiert",
        then="Python {python} und tmux. Sonst nichts. Dann <code>http://127.0.0.1:3200</code> öffnen.",
        self_b="Self-Host",
        self="Für immer kostenlos. MIT. Klonen, auf deiner Box laufen lassen, nur Loopback.",
        paid_b="Kein Self-Host",
        paid="Bezahlt kommt später. Sessions bleiben auf einer Maschine, die du kontrollierst. Wir hosten keine Shell für dich.",
        f24_b="24 MB resident.",
        f24="Kein Framework, kein <code>node_modules</code>, kein Build.",
        flop_b="Loopback mit Absicht.",
        flop="Wer das Panel erreicht, hat ein Terminal als du.",
        fring_b="Der Ring ist der Status.",
        fring="Dreht = arbeitet. Pulsiert = wartet auf dich. Nichts = idle.",
        cfg_h="Eine CLI hinzufügen ist Config, nie Code.",
        cfg_then="Seite neu laden. Wenn das jemals eine Codeänderung braucht, ist das Design gescheitert.",
        faq_h="Fragen",
        q1="Muss ich es ins Internet hängen?",
        a1="Nein. Es bindet nur Loopback. Setz einen Tunnel davor, wenn du von einer anderen Maschine willst. Nicht öffentlich hängen. Wer das Panel erreicht, hat ein Terminal als du.",
        q2="Was kostet es?",
        a2="Self-Host ist kostenlos, MIT. Später kostet die Version, die du nicht selbst aufsetzt. Das Panel läuft weiter auf deiner Maschine.",
        q3="Was brauche ich?",
        a3="Python {python} und tmux. Sonst nichts. Kein Node, kein Docker, kein Konto.",
        q4="Wie füge ich eine weitere Coding-CLI hinzu?",
        a4="Vier Zeilen in der Config. Seite neu laden. Wenn das Code braucht, ist das Design gescheitert.",
        get_h="Klonen",
        coffee='<a href="https://buymeacoffee.com/jdubb">Kauf mir einen Kaffee</a>, wenn es dir einen Nachmittag gespart hat.',
        search="Sessions suchen…",
        fold_work="Arbeit",
        fold_home="Privat",
        s1="Auth-Umbau",
        s2="Landing",
        s3="Migrationen",
        s4="Wochennotizen",
        waiting="wartet auf dich",
        term1="Ich höre hier auf. Der Session-Store sieht richtig aus;",
        term2="die Middleware lässt immer noch ein abgelaufenes Cookie durch.",
        term3='Soll ich <span class="file">app/auth.py</span> flicken, oder schauen wir es uns erst an?',
        prompt="Prompt oder Shell-Befehl…",
        run="Run",
        shot="CLIque: links Ordner mit CLI-Sessions, rechts ein Terminal. Der Ring ums Logo zeigt arbeitend, wartend oder idle.",
    ),
}

TMPL = """\
<!doctype html>
<html lang="{html_lang}">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="https://useclique.dev/{slug}/">
{hreflang}
<meta property="og:locale" content="{locale}">
<meta name="theme-color" content="#0E1116">
<link rel="icon" href="../brand/mark.svg" type="image/svg+xml">
<link rel="icon" href="../brand/favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="../brand/apple-touch-icon.png">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{ogdesc}">
<meta property="og:image" content="https://useclique.dev/brand/social-preview.png?v=2">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="640">
<meta name="twitter:image" content="https://useclique.dev/brand/social-preview.png?v=2">
<meta property="og:url" content="https://useclique.dev/{slug}/">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="../site.css?v=11">

<div class="page">
  <header>
    <a class="word" href="/{slug}/">
      <img src="../brand/mark.svg" width="20" height="20" alt="">
      CLIque
    </a>
    <nav>
      <a class="mit" href="https://github.com/thejdubb02/clique/blob/main/LICENSE">MIT</a>
      <a class="btn-gh btn-star" href="https://github.com/thejdubb02/clique" rel="noopener">
        {star} Star on GitHub
      </a>
      <a class="btn-gh" href="https://github.com/thejdubb02/clique" rel="noopener">
        {octo} View on GitHub
      </a>
    </nav>
  </header>

  <section class="hero">
    <p class="kicker">{kicker}</p>
    <h1>{h1}</h1>
    <p class="lede">{lede}</p>
    <p class="clis">{clis}</p>
    <p class="tag">{tag}</p>

    <div class="run">
      <div class="run-head">
        <span class="host">clique @ 127.0.0.1:3200</span>
        <button type="button" class="copybtn" hidden data-idle="{copy}" data-done="{copied}" data-copy="{clone}">{copy}</button>
      </div>
<pre><code><span class="ps">$</span> git clone https://github.com/thejdubb02/clique.git
<span class="ps">$</span> cd clique
<span class="ps">$</span> python3 -m clique password
<span class="ps">$</span> python3 -m clique</code></pre>
    </div>
    <p class="then">{then}</p>
    <p class="badges">
      <a href="https://github.com/thejdubb02/clique/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-5FA8F5?style=flat-square" alt="MIT"></a>
      <a href="https://github.com/thejdubb02/clique#quick-start"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square" alt="Python 3.11+"></a>
      <img src="https://img.shields.io/badge/tmux-required-22c55e?style=flat-square" alt="tmux">
      <a href="https://github.com/thejdubb02/clique/stargazers"><img src="https://img.shields.io/github/stars/thejdubb02/clique?style=flat-square" alt="GitHub stars"></a>
      <a href="https://github.com/thejdubb02/clique/graphs/contributors"><img src="https://img.shields.io/github/contributors/thejdubb02/clique?style=flat-square" alt="Contributors"></a>
      <a href="https://github.com/thejdubb02/clique/commits/main"><img src="https://img.shields.io/github/commit-activity/t/thejdubb02/clique?style=flat-square" alt="Commits"></a>
    </p>
  </section>

  <figure class="shot" aria-label="{shot}">
    <div class="app">
      <aside>
        <div class="ah">
          <img src="../brand/mark.svg" width="13" height="13" alt="">
          <span>CLIque</span>
        </div>
        <div class="search">{search}</div>
        <div class="tree">
          <div class="fold">{fold_work}</div>
          <div class="sess on">
            <span class="cli-status wait" style="--c:#D97757"><i style="-webkit-mask-image:url(../icons/claude.svg);mask-image:url(../icons/claude.svg)"></i></span>
            <span class="meta"><b>{s1}</b><small>~/proj/api</small></span>
          </div>
          <div class="sess">
            <span class="cli-status work" style="--c:#E8E8E8"><i style="-webkit-mask-image:url(../icons/grok.svg);mask-image:url(../icons/grok.svg)"></i></span>
            <span class="meta"><b>{s2}</b><small>~/proj/site</small></span>
          </div>
          <div class="sess">
            <span class="cli-status" style="--c:#1f6feb"><i style="-webkit-mask-image:url(../icons/gemini.svg);mask-image:url(../icons/gemini.svg)"></i></span>
            <span class="meta"><b>{s3}</b><small>~/proj/db</small></span>
          </div>
          <div class="fold">{fold_home}</div>
          <div class="sess">
            <span class="cli-status" style="--c:#10A37F"><i style="-webkit-mask-image:url(../icons/codex.svg);mask-image:url(../icons/codex.svg)"></i></span>
            <span class="meta"><b>{s4}</b><small>~/notes</small></span>
          </div>
        </div>
      </aside>
      <div class="pane">
        <div class="tabs">
          <div class="tab active" style="--cli:#D97757">{s1}</div>
          <div class="tab">{s2}</div>
          <div class="plus">+</div>
          <div class="stats">cpu 12% · mem 24 MB</div>
        </div>
        <div class="term">
          <div class="tline dim">claude · ~/proj/api · {waiting}</div>
          <div class="tline">{term1}</div>
          <div class="tline">{term2}</div>
          <div class="tline"></div>
          <div class="tline">{term3}</div>
          <div class="caret">▍</div>
        </div>
        <div class="bar">
          <span class="field">{prompt}</span>
          <span class="runbtn">{run}</span>
        </div>
      </div>
    </div>
  </figure>

  <section class="tracks">
    <div>
      <b>{self_b}</b>
      {self}
    </div>
    <div>
      <b>{paid_b}</b>
      {paid}
    </div>
  </section>

  <section class="facts">
    <div><b>{f24_b}</b> {f24}</div>
    <div><b>{flop_b}</b> {flop}</div>
    <div><b>{fring_b}</b> {fring}</div>
  </section>

  <section class="cfg">
    <h2>{cfg_h}</h2>
<pre><code>[cli.grok]
label   = "Grok CLI"
command = "grok"
args    = []
color   = "#E8E8E8"</code></pre>
    <p class="then">{cfg_then}</p>
  </section>

  <section class="faq">
    <h2>{faq_h}</h2>
    <details>
      <summary>{q1}</summary>
      <p>{a1}</p>
    </details>
    <details>
      <summary>{q2}</summary>
      <p>{a2}</p>
    </details>
    <details>
      <summary>{q3}</summary>
      <p>{a3}</p>
    </details>
    <details>
      <summary>{q4}</summary>
      <p>{a4}</p>
    </details>
  </section>

  <section class="get">
    <h2>{get_h}</h2>
    <div class="run">
      <div class="run-head">
        <span class="host">clique @ 127.0.0.1:3200</span>
        <button type="button" class="copybtn" hidden data-idle="{copy}" data-done="{copied}" data-copy="{clone}">{copy}</button>
      </div>
<pre><code><span class="ps">$</span> git clone https://github.com/thejdubb02/clique.git
<span class="ps">$</span> cd clique
<span class="ps">$</span> python3 -m clique password
<span class="ps">$</span> python3 -m clique</code></pre>
    </div>
    <p class="then gh-row">
      <a class="btn-gh btn-star" href="https://github.com/thejdubb02/clique" rel="noopener">
        {star} Star on GitHub
      </a>
      <a class="btn-gh" href="https://github.com/thejdubb02/clique" rel="noopener">
        {octo} View on GitHub
      </a>
    </p>
  </section>

  <footer>
    <p class="langs">{langs}</p>
    <p><a href="https://github.com/thejdubb02/clique">github.com/thejdubb02/clique</a> · MIT · <a href="https://github.com/thejdubb02">Justin Willhite</a></p>
    <p>{coffee}</p>
  </footer>
</div>

<script>
(function () {{
  document.querySelectorAll(".copybtn").forEach(function (btn) {{
    if (!navigator.clipboard) return;
    btn.hidden = false;
    btn.addEventListener("click", function () {{
      navigator.clipboard.writeText(btn.getAttribute("data-copy") || "").then(function () {{
        var done = btn.getAttribute("data-done") || "Copied";
        var idle = btn.getAttribute("data-idle") || "Copy";
        btn.textContent = done;
        setTimeout(function () {{ btn.textContent = idle; }}, 1400);
      }});
    }});
  }});
}})();
</script>
"""


def main() -> None:
    for slug, t in PAGES.items():
        out = ROOT / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        html = TMPL.format(
            slug=slug,
            hreflang=HREFLANG,
            star=STAR,
            octo=OCTO,
            clone=CLONE,
            clis=CLIS,
            langs=langs_html(t["html_lang"] if t["html_lang"] != "pt-BR" else "pt-BR"),
            **t,
        )
        out.write_text(html)
        print(f"wrote {out.relative_to(ROOT.parent)} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
