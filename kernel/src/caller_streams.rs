//! Caller-owned transport/2 media. Host grants and control finalization stay external.

use std::future::Future;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tokio::io::AsyncWriteExt;
use tokio::net::{UnixStream, unix::OwnedWriteHalf};
use tokio::sync::{Mutex as AsyncMutex, mpsc, watch};
use tokio::task::JoinHandle;
use tokio::time::{Instant, timeout_at};

use crate::streams::{HostObserver, INPUT_WINDOW, Packet, StreamError};
use crate::{handshake_v2, managed_v2, transport_v2};

type Result<T> = std::result::Result<T, StreamError>;

struct State {
    observer: HostObserver,
    closed: bool,
    failure: Option<StreamError>,
}

/// A call-bound caller channel with independent input half-close and output/end.
/// Parsing startup and opening a socket grants no authority: the host must
/// consume the actual parent-bound media grant before forwarding any effects.
pub struct CallerSession {
    state: Arc<Mutex<State>>,
    output: AsyncMutex<(mpsc::Receiver<Packet>, bool)>,
    writer: Arc<AsyncMutex<OwnedWriteHalf>>,
    failures: watch::Sender<Option<StreamError>>,
    tasks: Mutex<Vec<JoinHandle<()>>>,
    closing: AsyncMutex<()>,
    deadline: Option<Instant>,
}

async fn during<F: Future>(deadline: Option<Instant>, future: F) -> Result<F::Output> {
    if let Some(deadline) = deadline {
        timeout_at(deadline, future)
            .await
            .map_err(|_| StreamError::Timeout)
    } else {
        Ok(future.await)
    }
}

fn fail(state: &Mutex<State>, failures: &watch::Sender<Option<StreamError>>, error: StreamError) {
    if let Ok(mut state) = state.lock()
        && !state.closed
        && state.failure.is_none()
    {
        state.failure = Some(error);
        failures.send_replace(Some(error));
    }
}

impl CallerSession {
    /// Derive protected media/hello from startup and an actual host-assigned ID.
    /// None adds no overall generation timer. Connect/hello and partial-frame
    /// I/O retain separate bounded watchdogs; no stream is retried or reopened.
    pub async fn open(
        startup: &managed_v2::Record,
        call_id: &str,
        mode: &str,
        seconds: Option<f64>,
    ) -> Result<Self> {
        let observer = HostObserver::new(call_id, mode)?;
        if seconds
            .is_some_and(|value| !value.is_finite() || value <= 0.0 || value > 9_007_199_254_740.0)
        {
            return Err(StreamError::InvalidFrame);
        }
        let deadline = seconds
            .map(|seconds| {
                Instant::now()
                    .checked_add(Duration::from_secs_f64(seconds))
                    .ok_or(StreamError::InvalidFrame)
            })
            .transpose()?;
        let hello = handshake_v2::from_startup(startup, Some(call_id))
            .map_err(|_| StreamError::InvalidFrame)?;
        let value: serde_json::Value =
            serde_json::from_slice(startup.canonical()).map_err(|_| StreamError::InvalidFrame)?;
        let socket = value["stream_socket"]
            .as_str()
            .ok_or(StreamError::InvalidFrame)?;
        let handshake_seconds = value["manifest"]["bounds"]["startup_seconds"]
            .as_f64()
            .ok_or(StreamError::InvalidFrame)?;
        let handshake_deadline = Instant::now()
            .checked_add(Duration::from_secs_f64(handshake_seconds))
            .ok_or(StreamError::InvalidFrame)?;
        let handshake_deadline =
            deadline.map_or(handshake_deadline, |limit| limit.min(handshake_deadline));
        let stream = timeout_at(handshake_deadline, UnixStream::connect(socket))
            .await
            .map_err(|_| StreamError::Timeout)?
            .map_err(|_| StreamError::Transport)?;
        let (mut reader, mut writer) = stream.into_split();
        timeout_at(handshake_deadline, async {
            let hello = hello.canonical();
            writer
                .write_all(&(hello.len() as u32).to_be_bytes())
                .await?;
            writer.write_all(hello).await?;
            writer.flush().await
        })
        .await
        .map_err(|_| StreamError::Timeout)?
        .map_err(|_| StreamError::Transport)?;

        let state = Arc::new(Mutex::new(State {
            observer,
            closed: false,
            failure: None,
        }));
        let writer = Arc::new(AsyncMutex::new(writer));
        let (failures, _) = watch::channel(None);
        let (sender, receiver) = mpsc::channel(INPUT_WINDOW);
        let read_state = state.clone();
        let read_failures = failures.clone();
        let mut failure = failures.subscribe();
        let receive = tokio::spawn(async move {
            let result: Result<()> = async {
                loop {
                    let packet = tokio::select! {biased;
                        _=failure.changed()=>return Err(StreamError::Closed),
                        packet=during(deadline, transport_v2::read_packet(&mut reader))=>packet??,
                    };
                    read_state
                        .lock()
                        .map_err(|_| StreamError::Closed)?
                        .observer
                        .observe(&packet, false)?;
                    let ended = packet.is_end();
                    tokio::select! {biased;
                        _=failure.changed()=>return Err(StreamError::Closed),
                        result=sender.send(packet)=>result.map_err(|_| StreamError::Closed)?,
                    }
                    if ended {
                        return Ok(());
                    }
                }
            }
            .await;
            if let Err(error) = result {
                fail(&read_state, &read_failures, error);
            }
        });
        let mut tasks = vec![receive];
        if let Some(deadline) = deadline {
            let state = state.clone();
            let failures = failures.clone();
            let writer = writer.clone();
            tasks.push(tokio::spawn(async move {
                tokio::time::sleep_until(deadline).await;
                fail(&state, &failures, StreamError::Timeout);
                let _ = writer.lock().await.shutdown().await;
            }));
        }
        Ok(Self {
            state,
            output: AsyncMutex::new((receiver, false)),
            writer,
            failures,
            tasks: Mutex::new(tasks),
            closing: AsyncMutex::new(()),
            deadline,
        })
    }

    /// Admit exact portable projection/media before scheduling a native write.
    pub fn packet(&self, projection: &str, media: Vec<u8>) -> Result<Packet> {
        transport_v2::packet(projection, media)
    }

    /// Return portable JSON without changing raw media or portable numeric meaning.
    pub fn projection(&self, packet: &Packet) -> Result<String> {
        transport_v2::projection(packet)
    }

    fn alive(&self) -> Result<()> {
        if self
            .deadline
            .is_some_and(|deadline| Instant::now() >= deadline)
        {
            return Err(StreamError::Timeout);
        }
        let state = self.state.lock().map_err(|_| StreamError::Closed)?;
        if let Some(error) = state.failure {
            Err(error)
        } else if state.closed {
            Err(StreamError::Closed)
        } else {
            Ok(())
        }
    }

    /// Send a validated caller packet. Completed input is only a half-close.
    /// Concurrent writes serialize their sequence transitions and full bytes.
    pub async fn send_input(&self, packet: Packet) -> Result<()> {
        let mut fence = CancellationFence {
            session: self,
            completed: false,
        };
        let result = async {
            transport_v2::encode_packet(&packet)?;
            let mut failure = self.failures.subscribe();
            let mut writer = self.writer.lock().await;
            self.alive()?;
            self.state.lock().map_err(|_| StreamError::Closed)?.observer.observe(&packet,true)?;
            tokio::select! {biased;
                _=failure.changed()=>{self.alive()?;Err(StreamError::Closed)},
                result=during(self.deadline,transport_v2::write_packet(&mut *writer,&packet))=>result?,
            }
        }.await;
        if let Err(error) = result {
            fail(&self.state, &self.failures, error);
            self.close().await;
        }
        fence.completed = true;
        result
    }

    /// Receive provider packets; None requires a validated terminal and end.
    /// A terminal packet alone is not successful control-call finalization.
    pub async fn next_output(&self) -> Result<Option<Packet>> {
        let mut fence = CancellationFence {
            session: self,
            completed: false,
        };
        let result = async {
            self.alive()?;
            let mut failure = self.failures.subscribe();
            let mut output = self.output.lock().await;
            self.alive()?;
            if output.1 {
                return Ok(None);
            }
            let packet = tokio::select! {biased;
                _=failure.changed()=>{self.alive()?;return Err(StreamError::Closed)},
                packet=output.0.recv()=>packet.ok_or(StreamError::Transport)?,
            };
            self.alive()?;
            if packet.is_end() {
                output.1 = true;
                Ok(None)
            } else {
                Ok(Some(packet))
            }
        }
        .await;
        if let Err(error) = result {
            fail(&self.state, &self.failures, error);
            self.close().await;
        }
        fence.completed = true;
        result
    }

    /// Require consumed media end, then close owned resources. The caller must
    /// separately wait for the host's matching delegate_finished control frame.
    pub async fn finish(&self) -> Result<()> {
        let mut fence = CancellationFence {
            session: self,
            completed: false,
        };
        let result = async {
            self.alive()?;
            let output = self.output.lock().await;
            if !output.1
                || !self
                    .state
                    .lock()
                    .map_err(|_| StreamError::Closed)?
                    .observer
                    .finished()
            {
                return Err(StreamError::MissingTerminal);
            }
            Ok(())
        }
        .await;
        if let Err(error) = result {
            fail(&self.state, &self.failures, error);
        }
        self.close().await;
        fence.completed = true;
        result
    }

    fn abort(&self) {
        if let Ok(tasks) = self.tasks.lock() {
            for task in tasks.iter() {
                task.abort();
            }
        }
    }

    /// Fence pending operations and join every owned reader/timer before return.
    /// Repeated/concurrent close calls share the same cleanup ownership.
    pub async fn close(&self) {
        let mut fence = CancellationFence {
            session: self,
            completed: false,
        };
        let _closing = self.closing.lock().await;
        if let Ok(mut state) = self.state.lock() {
            state.closed = true;
        }
        self.failures.send_replace(Some(StreamError::Closed));
        let tasks = self
            .tasks
            .lock()
            .map(|mut tasks| std::mem::take(&mut *tasks))
            .unwrap_or_default();
        // Keep join ownership if this close future is itself cancelled. A later
        // close must still await the aborted tasks, not merely see an empty list.
        let mut tasks = ClosingTasks {
            session: self,
            tasks,
        };
        for task in &tasks.tasks {
            task.abort();
        }
        while let Some(task) = tasks.tasks.last_mut() {
            let _ = task.await;
            // A JoinHandle must never be polled again after Ready. Remove it
            // before another await can cancel this close and return ownership.
            tasks.tasks.pop();
        }
        let _ = self.writer.lock().await.shutdown().await;
        fence.completed = true;
    }
}

struct ClosingTasks<'a> {
    session: &'a CallerSession,
    tasks: Vec<JoinHandle<()>>,
}
impl Drop for ClosingTasks<'_> {
    fn drop(&mut self) {
        for task in &self.tasks {
            task.abort();
        }
        if let Ok(mut tasks) = self.session.tasks.lock() {
            tasks.append(&mut self.tasks);
        }
    }
}

impl Drop for CallerSession {
    fn drop(&mut self) {
        if let Ok(mut state) = self.state.lock() {
            state.closed = true;
        }
        self.failures.send_replace(Some(StreamError::Closed));
        self.abort();
    }
}

struct CancellationFence<'a> {
    session: &'a CallerSession,
    completed: bool,
}
impl Drop for CancellationFence<'_> {
    fn drop(&mut self) {
        if !self.completed {
            fail(
                &self.session.state,
                &self.session.failures,
                StreamError::Closed,
            );
            self.session.abort();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn cancelled_close_does_not_poll_a_completed_join_again() {
        let (entered, ready) = tokio::sync::oneshot::channel();
        let (release, gate) = std::sync::mpsc::channel();
        // A running blocking task cannot be aborted, giving the close future a
        // deterministic join boundary. Production stream tasks remain async.
        let blocked = tokio::task::spawn_blocking(move || {
            entered.send(()).unwrap();
            gate.recv_timeout(Duration::from_secs(3)).unwrap();
        });
        ready.await.unwrap();
        let fast = tokio::spawn(async {});
        while !fast.is_finished() {
            tokio::task::yield_now().await;
        }
        let (socket, _peer) = UnixStream::pair().unwrap();
        let (_reader, writer) = socket.into_split();
        let (_, receiver) = mpsc::channel(INPUT_WINDOW);
        let (failures, _) = watch::channel(None);
        let session = CallerSession {
            state: Arc::new(Mutex::new(State {
                observer: HostObserver::new("call", "bidirectional").unwrap(),
                closed: false,
                failure: None,
            })),
            output: AsyncMutex::new((receiver, false)),
            writer: Arc::new(AsyncMutex::new(writer)),
            failures,
            tasks: Mutex::new(vec![fast, blocked]),
            closing: AsyncMutex::new(()),
            deadline: None,
        };
        assert!(
            tokio::time::timeout(Duration::from_millis(25), session.close())
                .await
                .is_err()
        );
        release.send(()).unwrap();
        tokio::time::timeout(Duration::from_secs(2), session.close())
            .await
            .unwrap();
        session.close().await;
    }
}
