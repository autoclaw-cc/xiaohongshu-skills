"""小红书页面 CSS 选择器常量。"""

# ========== 登录 ==========
LOGIN_STATUS = ".main-container .user .link-wrapper .channel"
QRCODE_IMG = ".login-container .qrcode-img"

# ========== 首页 / 搜索 ==========
FILTER_BUTTON = "div.filter"
FILTER_PANEL = "div.filter-panel"

# ========== Feed 详情 ==========
COMMENTS_CONTAINER = ".comments-container"
PARENT_COMMENT = ".parent-comment"
NO_COMMENTS_TEXT = ".no-comments-text"
END_CONTAINER = ".end-container"
TOTAL_COMMENT = ".comments-container .total"
SHOW_MORE_BUTTON = ".show-more"
NOTE_SCROLLER = ".note-scroller"
INTERACTION_CONTAINER = ".interaction-container"

# 页面不可访问容器
ACCESS_ERROR_WRAPPER = ".access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper"

# ========== 评论输入 ==========
COMMENT_INPUT_TRIGGER = "div.input-box div.content-edit span"
COMMENT_INPUT_FIELD = "div.input-box div.content-edit p.content-input"
COMMENT_SUBMIT_BUTTON = "div.bottom button.submit"
REPLY_BUTTON = ".right .interactions .reply"

# ========== 点赞 / 收藏 ==========
LIKE_BUTTON = ".interact-container .left .like-lottie"
COLLECT_BUTTON = ".interact-container .left .reds-icon.collect-icon"

# ========== 发布页 ==========
UPLOAD_CONTENT = "div.upload-content"
CREATOR_TAB = "div.creator-tab"
UPLOAD_INPUT = ".upload-input"
FILE_INPUT = 'input[type="file"]'
TITLE_INPUT = "div.d-input input"
CONTENT_EDITOR = "div.ql-editor"
IMAGE_PREVIEW = ".img-preview-area .pr"
PUBLISH_BUTTON = ".publish-page-publish-btn button.bg-red"

# 标题/正文长度校验
TITLE_MAX_SUFFIX = "div.title-container div.max_suffix"
CONTENT_LENGTH_ERROR = "div.edit-container div.length-error"

# 可见范围
VISIBILITY_DROPDOWN = "div.permission-card-wrapper div.d-select-content"
VISIBILITY_OPTIONS = "div.d-options-wrapper div.d-grid-item div.custom-option"

# 定时发布
SCHEDULE_SWITCH = ".post-time-wrapper .d-switch"
DATETIME_INPUT = ".date-picker-container input"

# 原创声明
ORIGINAL_SWITCH_CARD = "div.custom-switch-card"
ORIGINAL_SWITCH = "div.d-switch"

# 标签联想
TAG_TOPIC_CONTAINER = "#creator-editor-topic-container"
TAG_FIRST_ITEM = ".item"

# 弹窗
POPOVER = "div.d-popover"

# ========== 用户主页 ==========
SIDEBAR_PROFILE = "div.main-container li.user.side-bar-component a.link-wrapper span.channel"
