<script setup lang="ts">
import { computed } from 'vue'
import { IconHome, IconCompass, IconListCheck, IconInbox, IconPencil, IconAdjustmentsHorizontal, IconSearch, IconFilter, IconArchive, IconTrash, IconCalendar, IconGift, IconSitemap, IconCloud, IconClock, IconBan, IconX, IconChevronUp, IconChevronDown, IconRefresh, IconArrowUpRight, IconSparkles } from '@tabler/icons-vue'
const props=withDefaults(defineProps<{name:string;size?:number}>(),{size:18})
const icons:Record<string,typeof IconHome>={home:IconHome,compass:IconCompass,queue:IconListCheck,inbox:IconInbox,pen:IconPencil,settings:IconAdjustmentsHorizontal,search:IconSearch,filter:IconFilter,archive:IconArchive,trash:IconTrash,calendar:IconCalendar,gift:IconGift,source:IconSitemap,cloud:IconCloud,clock:IconClock,ban:IconBan,close:IconX,up:IconChevronUp,down:IconChevronDown,refresh:IconRefresh,arrow:IconArrowUpRight,sparkles:IconSparkles}
const glyph=computed(()=>icons[props.name]||IconCompass)
</script>
<template><component :is="glyph" :size="size" stroke="1.7" aria-hidden="true" class="ui-icon"/></template>
