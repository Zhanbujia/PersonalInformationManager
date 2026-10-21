<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

const props = defineProps<{ source: string }>()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const rendered = computed(() => md.render(props.source || ''))
</script>

<template>
  <div class="markdown" v-html="rendered" />
</template>
