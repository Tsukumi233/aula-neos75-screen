"""Pin the approved v3 revision 2 image and reuse the v3b regression stages."""
import run_v3b as runner

APPROVED_SHA='cba6f2c716e184668371f1cdd1e2f618b1851bc1fe565629296b35d210c9cc1c'
PREVIOUS=runner.ROOT/'hwtest/20261107_152755_v3b_dev'

# These are fixed task constants, not caller-selectable firmware inputs.
runner.DEV_SHA=APPROVED_SHA
runner.OLD_SHA='c7d2a4c10438299f495a3f14989953961a6c9ee59dd6cbf7cec79d92a6bbb406'
runner.OLD_BYTES=277260
runner.OLD_DESCRIPTION='v3b c7d2a4c1'
runner.BUILD_LABEL='v3c cba6f2c7'
runner.PREVIOUS=PREVIOUS
runner.__doc__=__doc__

if __name__=='__main__': raise SystemExit(runner.main())
