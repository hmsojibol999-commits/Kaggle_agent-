# প্রথমবার চালু করার নির্দেশ (SETUP)

মোট সময়: প্রায় ৩০–৪৫ মিনিট। একবারই করতে হবে।
ফোনে করলে Chrome খুলে ⋮ মেনু থেকে **Desktop site** টিক দিন, কারণ GitHub-এর অনেক বোতাম ফোনের সাধারণ মোডে লুকানো থাকে।

## ধাপ ০: দুটি key আগে নিয়ে রাখুন

1. **Groq:** console.groq.com এ ঢুকে **API Keys** থেকে নতুন key বানান। এর পাশে **Models/Limits** পাতায় দেখে নিন কোন মডেল কত ব্যবহার করা যায়।
2. **Pexels:** pexels.com/api এ গিয়ে ফর্ম পূরণ করলে key পাবেন।

Key দুটি একটি নিরাপদ নোটে কপি করে রাখুন। চ্যাটে কাউকে পাঠাবেন না।

## ধাপ ১: public repo বানান

1. GitHub-এ ওপরের **+** চিহ্ন → **New repository**।
2. **Repository name:** `wordclip-studio` (অন্য নামও চলবে)।
3. **Public** বেছে নিন। (Public না হলে GitHub Pages ফ্রিতে চলে না।)
4. **Add a README file** এ টিক দিন। এতে `main` ব্রাঞ্চ তৈরি হয়ে যাবে।
5. **Create repository** চাপুন।

## ধাপ ২: ফাইল আপলোড

প্রথমে `wordclip-studio.zip` আনজিপ (Extract) করুন। ফোনে: **Files** অ্যাপে ZIP-এ ট্যাপ করে **Extract**। এরপর যেকোনো একটি পথ বেছে নিন।

### পথ ক: কম্পিউটার থেকে (সহজ)

1. আনজিপ করা ফোল্ডার খুলুন। লুকানো ফোল্ডার দেখান (Windows: **View → Hidden items**, Mac: `Cmd + Shift + .`)।
2. GitHub-এ আপনার repo-তে **Add file → Upload files**।
3. ফোল্ডারের **ভেতরের সব ফাইল ও ফোল্ডার** (`.github` সহ, কিন্তু `github-workflows` ফোল্ডার বাদে) একসঙ্গে টেনে এনে ছাড়ুন।
4. নিচে **Commit changes** চাপুন।

GitHub একবারে প্রায় ১০০টির বেশি ফাইল আপলোড করতে দেয় না। সীমা ছাড়ালে `tests` ফোল্ডার বাদ দিন (এটা ছাড়াই চলবে), অথবা দুই দফায় আপলোড করুন।

### পথ খ: ফোন থেকে (ফোল্ডার ধরে ধরে)

ফোনে পুরো ফোল্ডার আপলোড করা যায় না। তাই একেকটি ফোল্ডারের জন্য একেকবার আপলোড করুন। নিচের ঠিকানাগুলোর `USER` আর `REPO` বদলে নিজের নাম বসান। ঠিকানা ব্রাউজারে খুললে সরাসরি আপলোড পাতা আসবে, ফোল্ডার আগে থেকে না থাকলেও সমস্যা নেই।

| # | ঠিকানা | কোন ফাইল দেবেন |
|---|---|---|
| ১ | `https://github.com/USER/REPO/upload/main` | মূল ফোল্ডারের ফাইলগুলো: `config.yaml`, `requirements.txt`, সব `.md` ফাইল |
| ২ | `https://github.com/USER/REPO/upload/main/engine` | `engine` ফোল্ডারের সব ফাইল |
| ৩ | `https://github.com/USER/REPO/upload/main/docs` | `docs` ফোল্ডারের ফাইলগুলো (ভেতরের ফোল্ডার বাদে) |
| ৪ | `https://github.com/USER/REPO/upload/main/docs/fonts` | `docs/fonts`-এর ফাইল |
| ৫ | `https://github.com/USER/REPO/upload/main/docs/icons` | `docs/icons`-এর ফাইল |
| ৬ | `https://github.com/USER/REPO/upload/main/assets/fonts` | `assets/fonts`-এর ফাইল |
| ৭ | `https://github.com/USER/REPO/upload/main/.github/workflows` | ৫টি `.yml` ফাইল |

প্রতিবার ফাইল বাছাই শেষে নিচে **Commit changes** চাপুন।

**ধাপ ৭ নিয়ে সতর্কতা:** `.github` ফোল্ডারটি অনেক ফোনে লুকানো থাকে। তাই ZIP-এর ভেতরে একই ৫টি ফাইল `github-workflows` নামের আলাদা (দৃশ্যমান) ফোল্ডারেও দেওয়া আছে। ওখান থেকে ফাইল বেছে ৭ নম্বর ঠিকানায় আপলোড করুন। `github-workflows` ফোল্ডারটি নিজে repo-তে তোলার দরকার নেই।

এতেও না হলে শেষ উপায়: repo-তে **Add file → Create new file**, নামের ঘরে লিখুন `.github/workflows/prepare.yml`, ফাইলটি খুলে সব লেখা কপি করে বসান, **Commit changes**। এভাবে ৫টি ফাইলের জন্যই করুন।

### আপলোড ঠিক হয়েছে কি দেখুন

Repo-র মূল পাতায় এগুলো থাকতে হবে: `.github`, `assets`, `docs`, `engine`, `config.yaml`, `requirements.txt`। `.github/workflows` খুললে ৫টি `.yml` ফাইল দেখা যাবে।

## ধাপ ৩: Secrets আর Variables বসান

Repo খুলে ওপরে **Settings** ট্যাব।

**Secrets (গোপন key):**

1. বাঁ দিকের মেনুতে **Secrets and variables → Actions**।
2. **Secrets** ট্যাবে **New repository secret**।
3. **Name:** `GROQ_API_KEY` (হুবহু, বড় হাতের), **Secret:** আপনার Groq key → **Add secret**।
4. আবার **New repository secret**: **Name:** `PEXELS_API_KEY`, **Secret:** Pexels key → **Add secret**।

**Variables (মডেল ও ভয়েসের নাম):** এগুলো ঐচ্ছিক। না দিলে নিচের ডিফল্ট চলবে।

1. একই পাতায় **Variables** ট্যাব → **New repository variable**।
2. একটি একটি করে বানান:

| Name | Value (ডিফল্ট) |
|---|---|
| `GROQ_TEXT_MODEL` | `llama-3.3-70b-versatile` |
| `GROQ_TTS_MODEL` | `canopylabs/orpheus-v1-english` |
| `GROQ_TTS_VOICE_QUESTION` | `autumn` |
| `GROQ_TTS_VOICE_ANSWER` | `troy` |

**অনুমতি ঠিক করুন:** **Settings → Actions → General** এ নিচে নামুন। **Workflow permissions** এ **Read and write permissions** বেছে **Save** চাপুন।

**Actions চালু কি না দেখুন:** repo-র **Actions** ট্যাবে যান। সবুজ বোতামে “I understand my workflows, go ahead and enable them” লেখা থাকলে সেটি চাপুন।

## ধাপ ৪: GitHub Pages চালু করুন

1. **Settings → Pages**।
2. **Build and deployment** এর **Source** এ **Deploy from a branch**।
3. **Branch** এ `main` আর পাশের ফোল্ডারে `/docs` বেছে **Save**।
4. ১–২ মিনিট পরে পাতার ওপরে ঠিকানা দেখাবে: `https://USER.github.io/REPO/`। ঠিকানাটি ফোনে বুকমার্ক করুন।

## ধাপ ৫: নিজের জন্য সীমিত অনুমতির token বানান

এই token দিয়ে ওয়েব পাতা আপনার repo-তে কাজ চালু করবে। তাই **শুধু এই repo-র জন্য** বানান।

1. GitHub-এ প্রোফাইল ছবি → **Settings** → একদম নিচে **Developer settings**।
2. **Personal access tokens → Fine-grained tokens → Generate new token**।
3. **Token name:** `wordclip`। **Expiration:** আপনার ইচ্ছামতো (যেমন ৯০ দিন; মেয়াদ শেষ হলে নতুন বানাতে হবে)।
4. **Repository access** এ **Only select repositories** বেছে আপনার `wordclip-studio` repo বাছুন।
5. **Permissions → Repository permissions** এ:
   - **Actions:** `Read and write`
   - **Contents:** `Read and write`
   - **Metadata:** `Read-only` (এটি নিজে থেকেই দেওয়া থাকে)
6. **Generate token** চাপুন। যে `github_pat_...` লেখাটি আসবে সেটি **এখনই কপি করুন**, পরে আর দেখা যাবে না।

## ধাপ ৬: ওয়েব পাতায় token বসান

1. ধাপ ৪-এর ঠিকানাটি খুলুন।
2. ওপরে **Settings** চাপুন।
3. **Token** ঘরে কপি করা লেখা বসান। (github.io ঠিকানা থেকে খুললে repo-র নাম নিজে নিজে ধরা পড়ে। না পড়লে **Repo** ঘরে `USER/REPO` লিখুন।)
4. **সংরক্ষণ করুন** চাপুন, তারপর **Token পরীক্ষা করুন**। তিনটি সবুজ টিক আসতে হবে।
5. ফোনের হোম স্ক্রিনে রাখতে: Chrome ⋮ → **Add to Home screen**, অথবা Safari **Share → Add to Home Screen**।

Token শুধু আপনার ফোনের ব্রাউজারে থাকে। **Token মুছুন** বোতামে যেকোনো সময় মুছতে পারবেন।

## ধাপ ৭: পরীক্ষা চালান, তারপর প্রথম ভিডিও

1. **Settings → Setup পরীক্ষা চালান।** প্রায় ১ মিনিট পর রিপোর্ট আসবে:
   - সব সবুজ হলে ভালো।
   - হলুদ মানে চলবে, তবে কিছু বিকল্প ব্যবস্থায় (যেমন Groq ভয়েস না পেলে edge-tts) চলবে।
   - লাল মানে আগে ঠিক করতে হবে। কী করতে হবে নিচেই লেখা থাকে।
2. **Settings → ভয়েস টেস্ট চালান।** ৩টি মহিলা ও ৩টি পুরুষ ভয়েস শুনে পছন্দেরটি বেছে ধাপ ৩-এর Variables-এ নাম বদলে দিন (`GROQ_TTS_VOICE_QUESTION` মহিলা, `GROQ_TTS_VOICE_ANSWER` পুরুষ)।
3. **Home → Prepare** চাপুন। ছবি বাছুন, জিনিসের ওপর ট্যাপ করুন।
4. **Build** চাপুন। শেষে **ভিডিও ডাউনলোড করুন**।

প্রথম ভিডিওর আগে ছোটখাটো সমস্যা হওয়া স্বাভাবিক। TROUBLESHOOTING_BN.md দেখুন।

## ধাপ ৮: পরে কীভাবে বদলাবেন

| কী বদলাবেন | কীভাবে |
|---|---|
| ভয়েস | ধাপ ৩-এর Variables (`GROQ_TTS_VOICE_...`) অথবা `config.yaml` |
| থিম (রং) | ওয়েব পাতায় **Settings → ভিডিওর থিম** (“অটো” দিলে ৬টি রং ঘুরে ঘুরে আসে) |
| চ্যানেলের নাম (ভিডিওর শেষে) | `config.yaml`-এর `channel_handle` |
| প্রতি ভিডিওর শব্দ-সংখ্যা (ডিফল্ট ৩) | `config.yaml`-এর `words_per_video` |
| ভিডিও ডাউনলোড | Build শেষের **ভিডিও ডাউনলোড করুন** বোতাম। অথবা repo-র ডান পাশে **Releases** → সংশ্লিষ্ট Release → `video.mp4` |

`config.yaml` বদলাতে: GitHub-এ ফাইলটি খুলে পেনসিল আইকন → বদলান → **Commit changes**। বিস্তারিত CONFIG_BN.md-এ।
