import { RefreshCw } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import { AccountsPanel } from "./AccountsPanel";
import { JobsPanel } from "./JobsPanel";
import { LinkPanel } from "./LinkPanel";
import { OverviewPanel } from "./OverviewPanel";
import { RuntimePanel } from "./RuntimePanel";
import { StorageDetails } from "./OverviewStorage";
import { SubscriptionsPanel } from "./SubscriptionsPanel";
import { useMediaController } from "./useMediaController";
import { MusicPanel } from "./music/MusicPanel";
import { BooksPanel } from "./books/BooksPanel";
import { ComicsPanel } from "./comics/ComicsPanel";
import { tone, word } from "./wording";
import { mediaEngineState } from "./overviewPresentation";
import "./media.css";

export default function MediaPage({ section = 4 }: { section?: number }) {
  const media = useMediaController();
  if (section === 8) return <BooksPanel media={media} />;
  if (section === 9) return <ComicsPanel media={media} />;
  return (
    <div
      className={`media-page${section === 4 ? " media-overview-page" : ""}${section === 2 ? " scroll-page" : ""}`}
    >
      {section !== 5 && (
        <div className="media-header">
          {media.view && section !== 4 && (
            <StatusRail
              tone={tone(mediaEngineState(media.view.engine))}
              label={`视频下载引擎 · ${word(mediaEngineState(media.view.engine))}`}
            />
          )}
          <button
            className="button"
            disabled={media.loading || !!media.busy}
            onClick={() => {
              media.clearError();
              void media.refresh();
            }}
          >
            <RefreshCw aria-hidden="true" />
            读取状态
          </button>
        </div>
      )}
      {media.error && (
        <div className="media-message media-warning" role="alert">
          {media.error}
        </div>
      )}
      {media.notice && (
        <div className="media-message" role="status">
          {media.notice}
        </div>
      )}
      {media.stale && media.view && (
        <p className="media-warning" role="status">
          下面仍是上次成功读取的快照。连接恢复后会自动更新。
        </p>
      )}
      {media.loading && !media.view ? (
        <StatePanel kind="loading" title="正在读取视频订阅状态">
          <p>正在确认账号、目标库、订阅和下载任务。</p>
        </StatePanel>
      ) : !media.view ? (
        <StatePanel kind="error" title="暂时无法读取视频订阅">
          <p>重新读取后继续，后台既有任务不会因关闭页面而取消。</p>
        </StatePanel>
      ) : !media.view.configured && section !== 2 ? (
        <StatePanel kind="unconfigured" title="视频订阅服务尚未配置">
          <p>此部署还没有登记视频账号、下载引擎和发布目标。</p>
        </StatePanel>
      ) : section === 5 ? (
        <MusicPanel media={media} />
      ) : section === 4 ? (
        <OverviewPanel
          view={media.view}
          responseMs={media.responseMs}
          stale={media.stale}
        />
      ) : section === 6 ? (
        <RuntimePanel
          view={media.view}
          responseMs={media.responseMs}
          stale={media.stale}
        />
      ) : section === 7 ? (
        <StorageDetails view={media.view} />
      ) : section === 0 ? (
        <SubscriptionsPanel media={media} />
      ) : section === 1 ? (
        <LinkPanel
          media={media}
          onJobs={() => {
            window.location.hash = "#/media/jobs";
          }}
        />
      ) : section === 2 ? (
        <JobsPanel media={media} />
      ) : (
        <AccountsPanel media={media} />
      )}
    </div>
  );
}
