<template>
    <div class="route-container">
        <main class="watch-container" :class="{
                'watch-container--control-display': playerStore.is_control_display,
                'watch-container--panel-display': Utils.isSmartphoneVertical() || Utils.isTabletVertical() ? true : playerStore.is_panel_display,
                'watch-container--fullscreen': playerStore.is_fullscreen,
                'watch-container--document-pip': playerStore.is_document_pip,
                'watch-container--video': playback_mode === 'Video',
            }">
            <WatchNavigation />
            <div class="watch-content"
                 @mousemove="playerStore.event_emitter.emit('SetControlDisplayTimer', {event: $event, is_player_region_event: true})"
                 @touchmove="playerStore.event_emitter.emit('SetControlDisplayTimer', {event: $event, is_player_region_event: true})"
                 @click="playerStore.event_emitter.emit('SetControlDisplayTimer', {event: $event, is_player_region_event: true})">
                <WatchHeader :playback_mode="playback_mode" />
                <WatchPlayer :playback_mode="playback_mode" />
            </div>
            <WatchPanel :playback_mode="playback_mode" />
        </main>
        <KeyboardShortcutList :playback_mode="playback_mode" />
        <LShapedScreenCropSettings />
    </div>
</template>

<script lang="ts">
import { mapStores } from 'pinia';
import { defineComponent, PropType } from 'vue';

import WatchHeader from '@/components/Watch/Header.vue';
import KeyboardShortcutList from '@/components/Watch/KeyboardShortcutList.vue';
import LShapedScreenCropSettings from '@/components/Watch/LShapedScreenCropSettings.vue';
import WatchNavigation from '@/components/Watch/Navigation.vue';
import WatchPanel from '@/components/Watch/Panel.vue';
import WatchPlayer from '@/components/Watch/Player.vue';
import usePlayerStore from '@/stores/PlayerStore';
import useSettingsStore from '@/stores/SettingsStore';
import Utils from '@/utils';

export default defineComponent({
    name: 'Watch',
    components: {
        KeyboardShortcutList,
        LShapedScreenCropSettings,
        WatchHeader,
        WatchNavigation,
        WatchPanel,
        WatchPlayer,
    },
    props: {
        playback_mode: {
            type: String as PropType<'Live' | 'Video'>,
            required: true,
        },
    },
    data() {
        return {
            Utils: Object.freeze(Utils),
        };
    },
    computed: {
        ...mapStores(usePlayerStore, useSettingsStore),
    },
    watch: {
        'playerStore.is_panel_display': {
            handler() {
                this.settingsStore.settings.showed_panel_last_time = this.playerStore.is_panel_display;
            }
        }
    },
    created() {
        if ('virtualKeyboard' in navigator) {
            navigator.virtualKeyboard.overlaysContent = true;
            navigator.virtualKeyboard.ongeometrychange = (event: any) => {
                if (event.target.boundingRect.width === 0 && event.target.boundingRect.height === 0) {
                    this.playerStore.is_virtual_keyboard_display = false;
                } else {
                    this.playerStore.is_virtual_keyboard_display = true;
                }
            };
        }
        this.playerStore.startWatching();
    },
    beforeUnmount() {
        this.playerStore.stopWatching();
        if ('virtualKeyboard' in navigator) {
            navigator.virtualKeyboard.overlaysContent = false;
        }
    }
});
</script>

<style lang="scss">
/* --- グローバル上書きセクション --- */

/* 元のコントロール表示ロジック */
.watch-container.watch-container--control-display {
    .watch-player__dplayer {
        .dplayer-controller-mask, .dplayer-controller {
            opacity: 1 !important; visibility: visible !important;
            .dplayer-comment-box {
                left: calc(68px + 20px);
                @include tablet-vertical { left: calc(0px + 16px); }
                @include smartphone-horizontal { left: calc(0px + 16px); }
                @include smartphone-vertical { left: calc(0px + 16px); }
            }
        }
        .dplayer-notice {
            left: calc(68px + 30px); bottom: 62px;
            @include tablet-vertical { left: calc(0px + 16px); bottom: 62px !important; }
            @include smartphone-horizontal { left: calc(0px + 16px); }
            @include smartphone-vertical { left: calc(0px + 16px); bottom: 62px !important; }
        }
        .dplayer-info-panel {
            top: 82px; left: calc(68px + 30px);
            @include tablet-horizontal { left: calc(0px + 16px); }
            @include smartphone-horizontal { left: calc(0px + 16px); }
            @include smartphone-vertical { left: calc(0px + 16px); }
        }
        .dplayer-comment-setting-box {
            left: calc(68px + 20px);
            @include tablet-vertical { left: calc(0px + 16px); }
            @include smartphone-horizontal { left: calc(0px + 16px); }
            @include smartphone-vertical { left: calc(0px + 16px); }
        }
        .dplayer-mobile .dplayer-mobile-icon-wrap { opacity: 0.7 !important; visibility: visible !important; }
    }
}

.watch-container:not(.watch-container--control-display) {
    .watch-player__dplayer {
        .dplayer-danmaku { max-height: 100% !important; aspect-ratio: 16 / 9 !important; }
        .dplayer-notice { bottom: 20px !important; }
    }
}

/* Galaxy Z Fold / スマホ縦画面向け 設定パネル修正 */
.watch-container:not(.watch-container--fullscreen) {
    .watch-player__dplayer.dplayer-mobile {
        .dplayer-setting-box {
            @include smartphone-vertical {
                display: block !important;
                position: fixed !important;
                left: 0 !important; right: 0 !important; top: auto !important;
                bottom: 0 !important; 
                width: 100vw !important; height: auto !important;
                min-height: 320px !important; max-height: 70dvh !important;
                background: rgb(var(--v-theme-background)) !important;
                z-index: 20000 !important;
                transform: translateY(100%) !important;
                transition: transform 0.25s ease-out !important;
                padding-bottom: calc(env(safe-area-inset-bottom) + 24px) !important;
                overflow-y: auto !important;

                &.dplayer-setting-box-open { transform: translateY(0%) !important; }

                .dplayer-setting-item {
                    display: flex !important; height: 52px !important; align-items: center !important;
                    padding: 0 16px !important; border-bottom: 1px solid rgba(255, 255, 255, 0.08) !important;
                    .dplayer-setting-label { display: flex !important; align-items: center !important; flex: 1 !important; font-size: 15px !important; }
                    .dplayer-setting-right {
                        display: inline-flex !important; align-items: center !important; height: 100% !important;
                        svg, i, .dplayer-setting-icon { display: flex !important; align-items: center !important; justify-content: center !important; width: 20px !important; height: 20px !important; margin-left: 6px !important; fill: currentColor !important; }
                    }
                }
            }
        }
        .dplayer-setting-mask {
            @include smartphone-vertical { display: block !important; position: fixed !important; z-index: 19999 !important; }
        }
    }
}

/* ビデオ視聴・フルスクリーン等の既存スタイル */
.watch-container.watch-container--fullscreen {
    .watch-player__dplayer {
        .dplayer-controller { padding-left: 20px !important; }
        &.dplayer-mobile .dplayer-controller {
            padding-left: 30px !important;
            @include tablet-vertical { padding-left: 16px !important; }
            @include smartphone-horizontal { padding-left: 16px !important; }
            @include smartphone-vertical { padding-left: 16px !important; }
        }
        .dplayer-comment-box, .dplayer-comment-setting-box {
            left: 20px !important;
            @include tablet-vertical { left: 16px !important; }
            @include smartphone-horizontal { left: 16px !important; }
            @include smartphone-vertical { left: 16px !important; }
        }
    }
    .watch-header__back-icon { display: none !important; }
}

.watch-container.watch-container--video.watch-container--control-display {
    .watch-player__dplayer .dplayer-notice {
        bottom: 74px !important;
        &.dplayer-mobile {
            bottom: 71px !important;
            @include smartphone-vertical { bottom: 50px !important; }
        }
    }
}

.watch-container.watch-container--video.watch-container--fullscreen {
    .watch-player__dplayer {
        .dplayer-bar-wrap { width: calc(100% - (18px * 2)) !important; }
        &.dplayer-mobile .dplayer-bar-wrap {
            width: calc(100% - (30px * 2));
            @include tablet-horizontal { width: calc(100% - (30px * 2)) !important; }
            @include tablet-vertical { width: calc(100% - (18px * 2)) !important; }
            @include smartphone-horizontal { width: calc(100% - (18px * 2)) !important; }
            @include smartphone-vertical { width: calc(100% - (18px * 2)) !important; }
        }
    }
}

.watch-container .playing-in-pip {
    display: flex; flex-direction: column; gap: 20px; align-items: center; justify-content: center;
    width: 100%; color: rgb(var(--v-theme-text-darken-1)); font-size: 24px; padding: 20px;
    @include smartphone-vertical { aspect-ratio: 16 / 9; }
    &__close-button {
        padding: 12px 16px; border-radius: 8px; font-size: 15px;
        background: rgb(var(--v-theme-background-lighten-1)); transition: background-color 0.15s; cursor: pointer;
        &:hover { background: rgb(var(--v-theme-background-lighten-2)); }
    }
}
</style>

<style lang="scss" scoped>
/* --- Scoped スタイルセクション --- */

.route-container {
    height: 100vh !important;
    height: 100dvh !important;
    border-bottom: env(safe-area-inset-bottom) solid rgb(var(--v-theme-background));
    background: rgb(var(--v-theme-black)) !important;
    overflow: hidden;
    @include tablet-horizontal { border-bottom: env(safe-area-inset-bottom) solid rgb(var(--v-theme-black)); }
    @include smartphone-horizontal { border-bottom: env(safe-area-inset-bottom) solid rgb(var(--v-theme-black)); }
}

.watch-container {
    display: flex;
    width: calc(100% + 352px);
    height: 100%;
    transition: width 0.4s cubic-bezier(0.26, 0.68, 0.55, 0.99);

    /* タブレット・スマホ縦画面時は幅を100%に固定して縦並びにする */
    @include tablet-vertical { flex-direction: column; width: 100% !important; }
    @include smartphone-vertical { flex-direction: column; width: 100% !important; }
    @include smartphone-horizontal { width: calc(100% + 310px); }

    &.watch-container--control-display {
        .watch-content { cursor: auto !important; }
        .watch-navigation, .watch-header { opacity: 1 !important; visibility: visible !important; }
        .watch-player :deep() .watch-player__button { opacity: 1 !important; visibility: visible !important; }
    }

    &.watch-container--panel-display {
        width: 100% !important;
        .switch-button-panel .switch-button-icon { color: rgb(var(--v-theme-primary)); }

        /* 元のタッチデバイス向け描画制御 */
        .watch-panel { @media (hover: none) { content-visibility: auto; } }
    }

    /* iPad等で番組情報が消える問題の修正: タブレット縦画面時のレイアウトを明示 */
    @include tablet-vertical {
        width: 100% !important;
        .watch-panel { @media (hover: none) { content-visibility: auto; } }
    }
    @include smartphone-vertical {
        width: 100% !important;
        .watch-panel { @media (hover: none) { content-visibility: auto; } }
    }

    &.watch-container--fullscreen {
        .watch-navigation { display: none; }
        .watch-content .watch-header {
            padding-left: 30px;
            @include tablet-vertical { padding-left: 16px; }
            @include smartphone-horizontal { padding-left: 16px; }
        }
    }

    .watch-content {
        display: flex;
        position: relative;
        width: 100%;
        cursor: none;
        @include smartphone-vertical { z-index: 5; }
    }
}
</style>