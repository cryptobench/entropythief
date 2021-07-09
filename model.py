#!/usr/bin/python3
#entropythief
# author: krunch3r (KJM github.com/krunch3r76)
# license: General Poetic License (GPL3)
##external modules

# standard
import  aiohttp # to catch connection exception
import  base64
import  sys
import  os
import  termios
import  fcntl
import  asyncio
from    io          import StringIO
from    datetime    import timedelta
from    pathlib     import Path
from    typing      import AsyncIterable, Iterator
from    decimal     import  Decimal

# 3rd party
import  yapapi
from    yapapi          import log
from    yapapi.payload  import vm

# internal
import  utils
import  worker




ENTRYPOINT_FILEPATH = Path("/golem/run/worker.py")
kTASK_TIMEOUT = timedelta(minutes=10)
EXPECTED_ENTROPY = 128 # the number of bytes we can expect from any single provider's entropy pool
# TODO dynamically adjust based on statistical data







  #-------------------------------------------------#
 #           write_to_pipe                         #
#-------------------------------------------------#
# required by: entropythief()
async def write_to_pipe(fifoWriteEnd, thebytes):
    loop = asyncio.get_running_loop()

    try:
        await loop.run_in_executor(None, os.write, fifoWriteEnd, thebytes)
        # os.write(fifoWriteEnd, thebytes)
    except BrokenPipeError:
        raise









  #---------------------------------------------#
 #             steps                           #
#---------------------------------------------#
# required by:  entropythief
async def steps(ctx: yapapi.WorkContext, tasks: AsyncIterable[yapapi.Task]):

    async for task in tasks:
        try:
            #taskid=task.data
            ctx.run(ENTRYPOINT_FILEPATH.as_posix())

            # TODO ensure worker does not output on error

            future_results = yield ctx.commit()
            results = await future_results
            stdout=results[-1].stdout
            if len(stdout) > 0:
                task.accept_result(result=stdout)
            else:
                task.reject_result()

        except Exception as exception:
            print("STEPS UNHANDLED EXCEPTION", type(exception).__name__, file=sys.stderr)
            print(exception)
            raise
        finally:
            try:
                pass
            except FileNotFoundError:
                pass






  #---------------------------------------------#
 #             MySummaryLogger{}               #
#---------------------------------------------#
# Required by: entropythief
class MySummaryLogger(yapapi.log.SummaryLogger):
    costRunning = 0.0
    event_log_file=open('/dev/null')
    to_ctl_q = None

    def __init__(self, to_ctl_q):
        self.costRunning = 0.0
        self.to_ctl_q = to_ctl_q
        super().__init__()
        self.event_log_file = open("events.log", "w")

    def log(self, event: yapapi.events.Event) -> None:
        to_controller_msg = None
        if isinstance(event, yapapi.events.PaymentAccepted):
            self.costRunning += float(event.amount)
            to_controller_msg = {
                'cmd': 'update_total_cost', 'amount': self.costRunning}
        elif isinstance(event, yapapi.events.PaymentFailed):
            to_controller_msg = {
                'info': 'payment failed'
            }
        elif isinstance(event, yapapi.events.WorkerStarted):
            to_controller_msg = {
                'info': 'worker started'
            }
        elif isinstance(event, yapapi.events.WorkerFinished):
            to_controller_msg = {
                'info': 'worker finished'
            }
        elif isinstance(event, yapapi.events.AgreementCreated):
            to_controller_msg = {
                'event': 'AgreementCreated'
                ,'agr_id': event.agr_id
                ,'provider_id': event.provider_id
                ,'provider_info': event.provider_info.name
            }
        elif hasattr(event, 'agr_id'):
            to_controller_msg = {
                'event': event.__class__.__name__
                , 'agr_id': event.agr_id
                , 'struct': str(event)
            }
        else:
            
            # uncomment to log all the Event types as they occur to the specified file
            print(type(event), file=self.event_log_file)
            print(event, file=self.event_log_file)
            """
            if hasattr(event, 'agr_id'):
                agreement = { 'agr_id': event.agr_id
                             , 
                to_controller_msg = {
                    'agreement_event': {}   
                }
            """
        #/if
        if to_controller_msg:
            self.to_ctl_q.put(to_controller_msg)

        super().log(event)


    def __del__(self):
        self.event_log_file.close()




  ###############################################
 #             entropythief()                  #
###############################################
async def entropythief(args, from_ctl_q, fifoWriteEnd, MINPOOLSIZE, to_ctl_q, BUDGET, MAXWORKERS, IMAGE_HASH, TASK_TIMEOUT=kTASK_TIMEOUT):

    OP_STOP = False
    OP_PAUSE = False
    while not OP_STOP:
        await asyncio.sleep(0.05)
        mySummaryLogger = MySummaryLogger(to_ctl_q)
        # setup executor
        package = await vm.repo(
            image_hash=IMAGE_HASH, min_mem_gib=0.005, min_storage_gib=0.01
        )

        while (not OP_STOP): # can catch OP_STOP here and/or in outer
            await asyncio.sleep(0.05)
            if OP_PAUSE: # burn queue messages unless stop message seen
                if not from_ctl_q.empty():
                    qmsg = from_ctl_q.get_nowait()
                    print(qmsg, file=sys.stderr)
                    if 'cmd' in qmsg and qmsg['cmd'] == 'stop':
                        OP_STOP = True
                    elif 'cmd' in qmsg and qmsg['cmd'] == 'resume execution':
                        OP_PAUSE=False # currently resume execution is not part of the design, included for future designs
                continue # always rewind outer loop on OP_PAUSE
            async with yapapi.Golem(
                budget=BUDGET
                , subnet_tag=args.subnet_tag
                , network=args.network
                , driver=args.driver
                , event_consumer=mySummaryLogger.log
                , strategy = yapapi.strategy.LeastExpensiveLinearPayuMS(
                    max_fixed_price=Decimal("0.02"),
                    max_price_for={yapapi.props.com.Counter.CPU: Decimal("0.02"), yapapi.props.com.Counter.TIME: Decimal("0.02")}
            ) 
            ) as golem:
                OP_STOP = False
                while (not OP_STOP and not OP_PAUSE):
                    await asyncio.sleep(0.05)
                    if not from_ctl_q.empty():
                        qmsg = from_ctl_q.get_nowait()
                        print(qmsg, file=sys.stderr)
                        if 'cmd' in qmsg and qmsg['cmd'] == 'stop':
                            OP_STOP = True
                            continue
                        elif 'cmd' in qmsg and qmsg['cmd'] == 'set buflim':
                            MINPOOLSIZE = qmsg['limit']
                        elif 'cmd' in qmsg and qmsg['cmd'] == 'set maxworkers':
                            MAXWORKERS = qmsg['count']
                        elif 'cmd' in qmsg and qmsg['cmd'] == 'pause execution':
                            OP_PAUSE=True
                    #/if

                    # query length of pipe -> bytesInPipe
                    loop = asyncio.get_running_loop()
                    buf = bytearray(4)
                    await loop.run_in_executor(None, fcntl.ioctl, fifoWriteEnd, termios.FIONREAD, buf, 1)
                    bytesInPipe = int.from_bytes(buf, "little")

                    if bytesInPipe < int(MINPOOLSIZE):
                        bytes_needed = MINPOOLSIZE - bytesInPipe
                        # estimate how many workers it would take given the EXPECTED_ENTROPY per worker
                        workers_needed = int(bytes_needed/EXPECTED_ENTROPY)
                        if workers_needed == 0:
                            workers_needed = 1 # always at least one to get at least 1 byte
                        # adjust down workers_needed if exceeding max
                        if workers_needed > MAXWORKERS:
                            workers_needed = MAXWORKERS
                        # execute tasks
                        completed_tasks = golem.execute_tasks(
                            steps,
                            [yapapi.Task(data=taskid) for taskid in range(workers_needed)],
                            payload=package,
                            max_workers=workers_needed,
                            timeout=TASK_TIMEOUT
                        )
                        # generate results
                        async for task in completed_tasks:
                            if task.result:
                                randomBytes = base64.b64decode(task.result)
                                msg = randomBytes.hex()
                                to_ctl_cmd = {'cmd': 'add_bytes', 'hexstring': msg}
                                to_ctl_q.put(to_ctl_cmd)
                                await write_to_pipe(fifoWriteEnd, randomBytes)
                        #/async for
                    #/if
                #/while True
            #/while not OP_PAUSE
        #/while not OP_STOP














###########################################################################
#                               model__main                               #
#   main entry for the model                                              #
#   launches entropythief attaching message queues                        #
###########################################################################
def model__main(args, from_ctl_q, fifoWriteEnd, to_ctl_q, MINPOOLSIZE, MAXWORKERS, BUDGET, IMAGE_HASH, use_default_logger=True):
    """
        args := result of argparse.Namespace() from the controller/cli
        from_ctl_q := Queue of messages coming from controller
        fifoWriteEnd := named pipe where (valid) results are written
        to_ctl_q := Queue of messages going to controller
        MINPOOLSIZE := threshold count of random bytes above which workers temporarily stop
        MAXWORKERS := the maximum number of workers assigned at a time for results (may be reduced internally)
        BUDGET := the maxmum amount of GLM spendable per run
        IMAGE_HASH := the hash link to the vm that providers will run
    """

    # loop
    loop = asyncio.get_event_loop()

    # uncomment to output yapapi logger INFO events to stderr and INFO+DEBUG to args.log_fle
    if use_default_logger:
        yapapi.log.enable_default_logger(
            log_file=args.log_file
            , debug_activity_api=True
            , debug_market_api=True
            , debug_payment_api=True)
    task = loop.create_task(
        entropythief(
            args
            , from_ctl_q
            , fifoWriteEnd
            , MINPOOLSIZE
            , to_ctl_q
            , BUDGET
            , MAXWORKERS
            , IMAGE_HASH)
    )

    try:
        loop.run_until_complete(task)

    except yapapi.NoPaymentAccountError as e:
        handbook_url = (
            "https://handbook.golem.network/requestor-tutorials/"
            "flash-tutorial-of-requestor-development"
        )
        emsg = f"{utils.TEXT_COLOR_RED}" \
            f"No payment account initialized for driver `{e.required_driver}` " \
            f"and network `{e.required_network}`.\n\n" \
            f"See {handbook_url} on how to initialize payment accounts for a requestor node." \
            f"{utils.TEXT_COLOR_DEFAULT}"
        emsg += f"\nMaybe you forgot to invoke {utils.TEXT_COLOR_YELLOW}yagna payment init --sender{utils.TEXT_COLOR_DEFAULT}"
        msg = {'exception': emsg }
        to_ctl_q.put_nowait(msg)

    except aiohttp.client_exceptions.ClientConnectorError as e:
        _msg = str(e)
        _msg += "\ndid you forget to invoke " + utils.TEXT_COLOR_YELLOW + "yagna service run" + utils.TEXT_COLOR_DEFAULT + "?"
        msg = {'exception': "..." +  _msg }
        to_ctl_q.put_nowait(msg)
    except Exception as e:
        msg = {'exception': str(type(e)) + ": " + str(e) }
        to_ctl_q.put_nowait(msg)

    finally:
        cmd = {'cmd': 'stop'}
        to_ctl_q.put_nowait(cmd)
        task.cancel()
        try:
            loop.run_until_complete(task)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass

