"""Ready-made frame sources for `Screen.stream`: clock, system monitor, slideshow, GIF player.

Renderers take the image size from HELLO (`screen.capabilities.image_size`); PREVIEW_SIZE (the 480 x 360
panel) is the default when no device is involved.
"""
from ._common import PREVIEW_SIZE, ViewCycle
from .clock import clock_frames, render_clock
from .gif import gif_frames
from .slideshow import list_images, slideshow_frames
from .sysmon import VIEWS as SYSMON_VIEWS
from .sysmon import Sample, Sampler, render_dashboard, sysmon_frames

__all__ = ["PREVIEW_SIZE", "SYSMON_VIEWS", "Sample", "ViewCycle", "Sampler", "clock_frames", "gif_frames", "list_images", "render_clock",
           "render_dashboard", "slideshow_frames", "sysmon_frames"]
