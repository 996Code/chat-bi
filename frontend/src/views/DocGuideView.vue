<template>
  <div class="doc-guide">
    <!-- Mobile toggle -->
    <button class="toc-toggle" @click="showToc = !showToc">☰ 目录</button>

    <!-- TOC sidebar -->
    <aside v-if="showToc" class="toc-panel">
      <nav>
        <a
          v-for="h in headings"
          :key="h.id"
          :href="'#' + h.id"
          :class="['toc-link', 'level-' + h.level]"
          @click.prevent="scrollTo(h.id)"
        >{{ h.text }}</a>
      </nav>
    </aside>

    <!-- Content -->
    <main class="doc-content" v-html="rendered"></main>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { Marked } from 'marked'
import hljs from 'highlight.js'
import { markedHighlight } from 'marked-highlight'

const rendered = ref('')
const headings = ref<{ id: string; text: string; level: number }[]>([])
const showToc = ref(true)

let _headings: { id: string; text: string; level: number }[] = []

const markedInstance = new Marked(
  markedHighlight({
    langPrefix: 'hljs language-',
    highlight(code: string, lang: string) {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value
      }
      return hljs.highlightAuto(code).value
    },
  }),
  {
    gfm: true,
    renderer: {
      heading({ text, depth }: { text: string; depth: number }) {
        const slug = 'h-' + text.replace(/[^\w一-鿿]+/g, '-').toLowerCase()
        _headings.push({ id: slug, text, level: depth })
        return `<h${depth} id="${slug}">${text}</h${depth}>`
      },
    },
  },
)

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  if (window.innerWidth < 768) showToc.value = false
}

onMounted(async () => {
  try {
    const res = await fetch('/chat-bi/api/v1/docs/learning-guide')
    const data = await res.json()
    _headings = []
    rendered.value = markedInstance.parse(data.content) as string
    headings.value = _headings
    await nextTick()
    document.querySelectorAll('.doc-content pre code').forEach((el) => {
      hljs.highlightElement(el as HTMLElement)
    })
  } catch (e) {
    rendered.value = '<p style="color:red">加载失败，请确认后端已启动。</p>'
  }
})
</script>

<style>
@import 'highlight.js/styles/github-dark.css';

.doc-guide {
  display: flex;
  min-height: 100vh;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: #fff;
  color: #24292f;
}

/* TOC toggle (mobile) */
.toc-toggle {
  display: none;
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: 100;
  background: #24292f;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 8px 14px;
  font-size: 14px;
  cursor: pointer;
}

/* TOC panel */
.toc-panel {
  width: 260px;
  min-width: 260px;
  max-height: 100vh;
  overflow-y: auto;
  position: sticky;
  top: 0;
  border-right: 1px solid #d0d7de;
  padding: 20px 12px;
  background: #f6f8fa;
}

.toc-link {
  display: block;
  padding: 4px 8px;
  color: #0969da;
  text-decoration: none;
  font-size: 13px;
  line-height: 1.5;
  border-radius: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.toc-link:hover { background: #ddf4ff; }
.toc-link.level-2 { font-weight: 600; }
.toc-link.level-3 { padding-left: 20px; }
.toc-link.level-4 { padding-left: 32px; font-size: 12px; }

/* Main content */
.doc-content {
  flex: 1;
  max-width: 900px;
  padding: 32px 40px 80px;
  margin: 0 auto;
  line-height: 1.7;
  font-size: 15px;
  overflow-wrap: break-word;
}

.doc-content h1 { font-size: 26px; border-bottom: 2px solid #d0d7de; padding-bottom: 8px; margin-top: 40px; }
.doc-content h2 { font-size: 22px; border-bottom: 1px solid #d0d7de; padding-bottom: 6px; margin-top: 36px; }
.doc-content h3 { font-size: 18px; margin-top: 28px; }
.doc-content h4 { font-size: 16px; margin-top: 20px; }

.doc-content code {
  background: #eff1f3;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
  font-family: 'SF Mono', 'Fira Code', Consolas, monospace;
}

.doc-content pre {
  background: #0d1117;
  border-radius: 8px;
  padding: 16px;
  overflow-x: auto;
  margin: 16px 0;
}

.doc-content pre code {
  background: none;
  padding: 0;
  color: #e6edf3;
  font-size: 13px;
  line-height: 1.6;
}

.doc-content table {
  border-collapse: collapse;
  width: 100%;
  margin: 16px 0;
  font-size: 14px;
}

.doc-content th, .doc-content td {
  border: 1px solid #d0d7de;
  padding: 8px 12px;
  text-align: left;
}

.doc-content th { background: #f6f8fa; font-weight: 600; }

.doc-content blockquote {
  border-left: 4px solid #0969da;
  margin: 16px 0;
  padding: 12px 20px;
  background: #ddf4ff;
  border-radius: 0 6px 6px 0;
}

.doc-content blockquote code {
  background: #b6e3ff;
}

.doc-content img { max-width: 100%; }
.doc-content a { color: #0969da; text-decoration: none; }
.doc-content a:hover { text-decoration: underline; }

.doc-content > table {
  display: block;
  overflow-x: auto;
}

/* Mobile */
@media (max-width: 768px) {
  .toc-toggle { display: block; }
  .toc-panel {
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    z-index: 90;
    box-shadow: 2px 0 12px rgba(0,0,0,.15);
  }
  .doc-content {
    padding: 60px 16px 40px;
    max-width: 100%;
  }
}
</style>
