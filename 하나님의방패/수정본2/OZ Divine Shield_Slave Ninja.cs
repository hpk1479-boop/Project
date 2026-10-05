using System;
using System.Collections.Generic;
using System.IO.MemoryMappedFiles;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using NinjaTrader.Cbi;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;

namespace NinjaTrader.Custom.AddOns
{
    public class OzCopyReceiverMenuItem : AddOnBase
    {
        private NTMenuItem menuItem;
        private NTMenuItem existingMenuItem;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "OZ Divine Shield - Multi-Account Receiver AddOn R12";
                Name        = "OZCopyReceiver";
            }
        }

        protected override void OnWindowCreated(Window window)
        {
            NTWindow ntWindow = window as NTWindow;
            if (ntWindow == null || ntWindow.GetType().Name != "ControlCenter") return;

            existingMenuItem = ntWindow.FindFirst("ControlCenterMenuItemNew") as NTMenuItem;
            if (existingMenuItem == null) return;

            foreach (var item in existingMenuItem.Items)
            {
                NTMenuItem mi = item as NTMenuItem;
                if (mi != null && mi.Header != null && mi.Header.ToString() == "OZ Multi Account Receiver")
                    return; 
            }

            menuItem = new NTMenuItem
            {
                Header = "OZ Multi Account Receiver",
                Style  = Application.Current.TryFindResource("MainMenuItem") as Style
            };

            menuItem.Click += (s, e) =>
            {
                Core.Globals.RandomDispatcher.InvokeAsync(() =>
                {
                    OzMultiAccountWindow.Open();
                });
            };

            existingMenuItem.Items.Add(menuItem);
        }

        protected override void OnWindowDestroyed(Window window)
        {
            if (menuItem != null && existingMenuItem != null)
            {
                existingMenuItem.Items.Remove(menuItem);
                menuItem = null;
            }
        }
    }

    internal enum OzCommState { RUNNING, TEMP_DISCONNECTED, HARD_HOLD }
    internal static class Oz11
    {
        internal static readonly System.Globalization.CultureInfo Inv = System.Globalization.CultureInfo.InvariantCulture;
        internal static bool Finite(double x) { return !double.IsNaN(x) && !double.IsInfinity(x); }
        internal static bool Fresh(ulong now, ulong tick, ulong limit = 5000) { return tick > 0 && now >= tick && now - tick <= limit; }
        private static readonly System.Collections.Concurrent.ConcurrentQueue<string[]> messages=new System.Collections.Concurrent.ConcurrentQueue<string[]>();
        private static int messageCount;
        private static readonly AutoResetEvent logWake=new AutoResetEvent(false);
        private static readonly Thread logThread;
        private static int logDropped;
        static Oz11()
        {
            logThread=new Thread(()=>
            {
                for(;;)
                {
                    string[] group;if(!messages.TryDequeue(out group)){logWake.WaitOne(250);continue;}
                    Interlocked.Decrement(ref messageCount);
                    try{foreach(string line in group)NinjaTrader.Code.Output.Process(line,PrintTo.OutputTab1);}
                    catch{Interlocked.Increment(ref logDropped);}
                }
            }){IsBackground=true,Name="OZ grouped logger"};logThread.Start();
        }
        private static void EnqueueLog(string[] group)
        {
            if(Interlocked.Increment(ref messageCount)>16384){Interlocked.Decrement(ref messageCount);Interlocked.Increment(ref logDropped);return;}
            messages.Enqueue(group);logWake.Set();
        }
        internal static void Log(string text){EnqueueLog(new[]{text});}
        internal static void Burst(string tag,string reason,string account)
        {
            string line="========== ["+tag+"] "+reason+" | "+account+" ==========";
            EnqueueLog(Enumerable.Repeat(line,5).ToArray());
        }
        internal static string Hash(string text)
        {
            using (var sha = System.Security.Cryptography.SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "");
        }
        internal static string PathFor(string kind, string key)
        {
            string folder = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OZCopy", "R11");
            System.IO.Directory.CreateDirectory(folder);
            return System.IO.Path.Combine(folder, kind + "_" + Hash(key) + ".xml");
        }
        internal static void SaveXml(string path, System.Xml.Linq.XDocument doc)
        {
            string tmp = path + ".tmp";
            using (var f = new System.IO.FileStream(tmp, System.IO.FileMode.Create, System.IO.FileAccess.Write, System.IO.FileShare.None))
            { doc.Save(f); f.Flush(true); }
            if (System.IO.File.Exists(path)) System.IO.File.Replace(tmp, path, path + ".bak");
            else System.IO.File.Move(tmp, path);
        }
        internal static DateTime ExecutionUtc(DateTime value)
        {
            if(value.Kind==DateTimeKind.Utc)return value;
            if(value.Kind==DateTimeKind.Local)return value.ToUniversalTime();
            var zone=Core.Globals.GeneralOptions.TimeZoneInfo;
            if(zone.IsInvalidTime(value)||zone.IsAmbiguousTime(value))throw new InvalidOperationException("EXECUTION_TIME_ZONE_AMBIGUOUS");
            return TimeZoneInfo.ConvertTimeToUtc(DateTime.SpecifyKind(value,DateTimeKind.Unspecified),zone);
        }
        internal static string ConnectionName(Account a)
        { return a != null && a.Connection != null && a.Connection.Options != null ? a.Connection.Options.Name : "UNCONNECTED"; }
        internal static bool BrokerConnected(Account a)
        { return a != null && a.Connection != null && a.Connection.Status == ConnectionStatus.Connected; }
    }
    // A bounded mailbox and a dedicated executor per account. The common MMF reader never waits for Submit/Cancel.
    internal sealed class OzSerialExecutor : IDisposable
    {
        private readonly Queue<Action> queue = new Queue<Action>();
        private readonly object gate = new object();
        private readonly AutoResetEvent wake = new AutoResetEvent(false);
        private readonly Action<Exception> failed;
        private readonly Action tick;
        private volatile bool stopped;
        private Thread thread;
        internal OzSerialExecutor(Action periodic, Action<Exception> failure) { tick = periodic; failed = failure; }
        internal void Start(string name)
        {
            thread = new Thread(Run) { IsBackground = true, Name = name };
            thread.SetApartmentState(ApartmentState.STA); thread.Start();
        }
        internal bool TryPost(Action a)
        {
            lock (gate) { if (stopped || queue.Count >= 8192) return false; queue.Enqueue(a); wake.Set();return true; }
        }
        internal void InvokeAsync(Action a) { if (!TryPost(a)) failed(new InvalidOperationException("ACCOUNT_CALLBACK_QUEUE_FULL")); }
        internal void Wake() { lock (gate) { if (!stopped) wake.Set(); } }
        private void Run()
        {
            try
            {
                while (!stopped)
                {
                    for (int i = 0; i < 128 && !stopped; i++)
                    {
                        Action a = null; lock (gate) { if (queue.Count > 0) a = queue.Dequeue(); }
                        if (a == null) break;
                        try { a(); } catch (Exception ex) { failed(ex); }
                    }
                    try { tick(); } catch (Exception ex) { failed(ex); }
                    wake.WaitOne(20);
                }
            }
            finally { lock(gate){stopped=true;queue.Clear();wake.Dispose();} }
        }
        public void Dispose() { lock(gate){if(stopped)return;stopped=true;queue.Clear();wake.Set();} }
    }
    // Immutable enable gate: reading/toggling requires no account-queue, UI or monitoring lock.
    internal sealed class OzEnableSelection
    {
        internal readonly bool Enabled;
        internal readonly ulong Since,Revision;
        internal OzEnableSelection(bool enabled,ulong since,ulong revision){Enabled=enabled;Since=since;Revision=revision;}
    }
    internal static class OzSelection12
    {
        internal static bool All(IEnumerable<bool> selected)
        {bool any=false;foreach(bool value in selected){any=true;if(!value)return false;}return any;}
        internal static bool ContainsAccount(IEnumerable<string> names,string name)
        {return names.Any(v=>string.Equals(v.Trim(),name.Trim(),StringComparison.OrdinalIgnoreCase));}
    }
    // Only settings persistence (not execution/PnL durability) moves here. No trade thread waits for this worker.
    internal static class OzSettingsWriter12
    {
        private static readonly System.Collections.Concurrent.ConcurrentQueue<Action> queue=new System.Collections.Concurrent.ConcurrentQueue<Action>();
        private static readonly AutoResetEvent wake=new AutoResetEvent(false);
        private static int count;
        static OzSettingsWriter12()
        {
            var t=new Thread(()=>
            {
                for(;;){Action a;if(!queue.TryDequeue(out a)){wake.WaitOne(250);continue;}Interlocked.Decrement(ref count);try{a();}catch(Exception ex){Oz11.Log("[SETTINGS_SAVE_ERROR] "+ex.Message);}}
            }){IsBackground=true,Name="OZ settings persistence",Priority=ThreadPriority.BelowNormal};t.Start();
        }
        internal static bool Post(Action action)
        {
            if(Interlocked.Increment(ref count)>1024){Interlocked.Decrement(ref count);return false;}
            queue.Enqueue(action);wake.Set();return true;
        }
    }
    internal sealed class OzAccountSettings
    {
        internal bool Enabled;
        internal double Risk = 200, DailyLossPercent = 2.5;
        internal int MaxPositions = 1, DailyLossTrades = 4, ConsecutiveLosses = 2, CooldownMinutes = 60;
        internal string Mnq = "MNQ 12-26", Mgc = "MGC 12-26", DayZone = TimeZoneInfo.Local.Id;
        internal int DayStartMinutes;
        internal OzAccountSettings Copy() { return (OzAccountSettings)MemberwiseClone(); }
        internal void Validate()
        {
            if (!Oz11.Finite(Risk) || Risk <= 0 || !Oz11.Finite(DailyLossPercent) || DailyLossPercent <= 0 || DailyLossPercent > 100 ||
                MaxPositions < 0 || DailyLossTrades < 0 || ConsecutiveLosses < 0 || CooldownMinutes < 0 || CooldownMinutes > 10080 ||
                DayStartMinutes < 0 || DayStartMinutes >= 1440 || string.IsNullOrWhiteSpace(Mnq) || string.IsNullOrWhiteSpace(Mgc))
                throw new ArgumentException("INVALID_ACCOUNT_SETTINGS");
            TimeZoneInfo.FindSystemTimeZoneById(DayZone);
        }
    }
    internal sealed class OzAccountStatus
    {
        internal string RegistryConnection, Account, Connection, State = "TEMP_DISCONNECTED", Reason = "STARTING", Session = "", RequestSession = "";
        internal bool Enabled, LinkReady, EntryReady, CanStop, BrokerOnline;
        internal ulong Tick, Accepted, Request, SourceLogin;
        internal double Pnl, Equity;
        internal int Positions, DailyLosses, ConsecutiveLosses;
        internal double CooldownLeft;
    }
    internal sealed class OzControl11 : IDisposable
    {
        internal const int Bytes = 33024, Header = 256, Slot = 512, Count = 64;
        internal const uint Magic = 0x3131524F, AckMagic = 0x3131414F;
        internal sealed class Request { internal ulong Id, Tick, Source; internal string Session, Server; internal bool MasterReady=true; }
        private readonly string stream;
        private MemoryMappedFile file; private MemoryMappedViewAccessor view; private Mutex mutex;
        private readonly byte[] h = new byte[256]; private readonly byte[] owner = Guid.NewGuid().ToByteArray();
        private readonly Dictionary<string, int> assigned = new Dictionary<string, int>();
        private bool ownerPrepared;
        internal OzControl11(string id) { stream = id; }
        internal static void P32(byte[] b, int p, uint n) { for (int j = 0; j < 4; j++) b[p + j] = (byte)(n >> (j * 8)); }
        internal static void P64(byte[] b, int p, ulong n) { for (int j = 0; j < 8; j++) b[p + j] = (byte)(n >> (j * 8)); }
        internal static uint U32(byte[] b, int p) { return OzMasterReader.U32(b, p); }
        internal static ulong U64(byte[] b, int p) { return OzMasterReader.U64(b, p); }
        internal static bool Zero(byte[] b, int p, int n) { for (int i = p; i < p + n; i++) if (b[i] != 0) return false; return true; }
        internal static bool Same(byte[] a, int p, byte[] b, int q, int n) { for (int i = 0; i < n; i++) if (a[p+i] != b[q+i]) return false; return true; }
        internal static void Text(byte[] b, int p, int max, int lp, string s)
        {
            byte[] a = Encoding.UTF8.GetBytes(s ?? "");
            if (a.Length > max || (s ?? "").IndexOfAny(new[] { '\r', '\n', '\0' }) >= 0) throw new FormatException("CONTROL_TEXT_SIZE");
            P32(b, lp, (uint)a.Length); Buffer.BlockCopy(a, 0, b, p, a.Length);
        }
        internal static string Text(byte[] b, int p, int lp) { return new UTF8Encoding(false, true).GetString(b, p, (int)U32(b, lp)); }
        internal static byte[] HexBytes(string s)
        {
            if (s == null || s.Length != 32) return new byte[16];
            return Enumerable.Range(0, 16).Select(i => Convert.ToByte(s.Substring(i*2,2),16)).ToArray();
        }
        internal static bool HeaderValid(byte[] b)
        {
            uint n = U32(b,72);
            return U32(b,0)==Magic && U32(b,4)==1 && U32(b,8)==Bytes && U32(b,12)==256 && U32(b,16)==512 && U32(b,20)==64 &&
                U64(b,24)>0 && !Zero(b,32,16) && n>0 && n<=96 && Zero(b,80+(int)n,96-(int)n) && U32(b,184)<=3 && Zero(b,188,68) &&
                U32(b,76)==OzMasterReader.Crc(b,0,256,76);
        }
        internal static bool AckValid(byte[] b)
        {
            uint a=U32(b,72),s=U32(b,76),r=U32(b,80),p=U32(b,8);
            return U32(b,0)==AckMagic && (U32(b,4)==1||U32(b,4)==2) && (p==1||p==2) && U32(b,12)<=6 && a>0 && a<=96 && s>0 && s<=96 && r<=128 &&
                !Zero(b,56,16) && U32(b,440)<=1 &&
                ((U32(b,4)==1&&Zero(b,444,68))||(U32(b,4)==2&&p==2&&U32(b,444)<=1&&U64(b,448)>0&&Zero(b,464,48))) && U32(b,84)==OzMasterReader.Crc(b,0,512,84);
        }
        private bool Open()
        {
            if (view != null) return true;
            try
            {
                string root="Local\\OZCopy.Control11."+stream;
                file=MemoryMappedFile.OpenExisting(root+".State",MemoryMappedFileRights.ReadWrite);
                mutex=Mutex.OpenExisting(root+".Lock",System.Security.AccessControl.MutexRights.Synchronize|System.Security.AccessControl.MutexRights.Modify);
                view=file.CreateViewAccessor(0,Bytes,MemoryMappedFileAccess.ReadWrite);return true;
            }
            catch { Dispose();return false; }
        }
        private bool Take() { try { return mutex.WaitOne(0); } catch (AbandonedMutexException) { return true; } }
        internal Request ReadRequest()
        {
            if(!Open()) return null;bool owns=false;
            try { owns=Take();if(!owns)return null;view.ReadArray(0,h,0,256); }
            catch { return null; }
            finally { if(owns&&mutex!=null) mutex.ReleaseMutex(); }
            if(!HeaderValid(h)||!Oz11.Fresh(OzMasterReader.Tick(),U64(h,176))) return null;
            return new Request { Id=U64(h,48),Tick=U64(h,56),Source=U64(h,24),Session=BitConverter.ToString(h,32,16).Replace("-",""),Server=Text(h,80,72),MasterReady=U32(h,184)==1 };
        }
        private bool PrepareOwner()
        {
            if(ownerPrepared)return true;
            byte[] all=new byte[Bytes-256];bool owns=false;
            try{owns=Take();if(!owns)return false;view.ReadArray(256,all,0,all.Length);}
            finally{if(owns)mutex.ReleaseMutex();}
            for(int i=0;i<Count;i++)
            {
                byte[] prior=new byte[Slot];Buffer.BlockCopy(all,i*Slot,prior,0,Slot);
                if(!AckValid(prior)||U32(prior,8)!=2||Same(prior,56,owner,0,16))continue;
                bool locked=false;
                try
                {
                    locked=Take();if(!locked)return false;
                    byte[] actual=new byte[Slot];view.ReadArray(Header+i*Slot,actual,0,Slot);
                    if(Same(actual,0,prior,0,Slot))view.WriteArray(Header+i*Slot,new byte[Slot],0,Slot);
                }
                finally{if(locked)mutex.ReleaseMutex();}
            }
            ownerPrepared=true;return true;
        }
        internal bool RemoveAccount(string key)
        {
            if(!Open())return false;int slot;if(!assigned.TryGetValue(key,out slot))return true;
            bool owns=false;
            try
            {
                owns=Take();if(!owns)return false;byte[] prior=new byte[Slot];view.ReadArray(Header+slot*Slot,prior,0,Slot);
                if(U32(prior,8)==2&&Same(prior,56,owner,0,16))view.WriteArray(Header+slot*Slot,new byte[Slot],0,Slot);
                assigned.Remove(key);return true;
            }
            finally{if(owns)mutex.ReleaseMutex();}
        }
        internal void ClearOwned()
        {
            try{foreach(string key in assigned.Keys.ToArray())RemoveAccount(key);}
            catch(Exception ex){Oz11.Log("[REGISTRY_CLOSE_PENDING] "+ex.GetType().Name);}
        }
        internal void WriteStatus(OzAccountStatus status,bool enabled,ulong membershipRevision)
        {
            if(status==null||!Open()||!PrepareOwner()) return;
            string key=status.Account+"@"+status.RegistryConnection;int slot;
            if(!assigned.TryGetValue(key,out slot))
            {
                byte[] all=new byte[Bytes-256];bool owns=false;
                try { owns=Take();if(!owns)return;view.ReadArray(256,all,0,all.Length); }
                finally { if(owns)mutex.ReleaseMutex(); }
                int empty=-1;slot=-1;
                for(int i=0;i<64;i++)
                {
                    byte[] a=new byte[512];Buffer.BlockCopy(all,i*512,a,0,512);
                    if(Zero(a,0,512)&&empty<0)empty=i;
                    if(AckValid(a)&&U32(a,8)==2&&Text(a,96,72)==status.Account&&Text(a,192,76)==status.RegistryConnection){slot=i;break;}
                }
                if(slot<0)slot=empty;if(slot<0)return;
            }
            byte[] b=new byte[512];P32(b,0,AckMagic);P32(b,4,2);P32(b,8,2);
            bool masterReady=HeaderValid(h)&&U32(h,184)==1&&Oz11.Fresh(OzMasterReader.Tick(),U64(h,176));
            uint st=!enabled&&status.CanStop?6U:masterReady&&status.LinkReady?(status.EntryReady?1U:4U):status.State=="HARD_HOLD"?3U:2U;
            P32(b,12,st);P64(b,24,status.Request);P64(b,32,status.Tick); // EXECUTOR tick, never fabricated by the hub
            Buffer.BlockCopy(HexBytes(status.RequestSession),0,b,40,16);Buffer.BlockCopy(owner,0,b,56,16);P64(b,88,status.SourceLogin);
            Text(b,96,96,72,status.Account);Text(b,192,96,76,status.RegistryConnection);Text(b,288,128,80,status.Reason);
            Buffer.BlockCopy(HexBytes(status.Session),0,b,416,16);P64(b,432,status.Accepted);P32(b,440,masterReady&&enabled&&status.EntryReady?1U:0U);
            P32(b,444,enabled?1U:0U);P64(b,448,OzMasterReader.Tick());P64(b,456,membershipRevision); // membership != executor liveness
            P32(b,84,OzMasterReader.Crc(b,0,512,84));bool locked=false;
            try
            {
                locked=Take();if(!locked)return;
                byte[] old=new byte[512];view.ReadArray(256+slot*512,old,0,512);
                // Compare fixed-size identity bytes under lock; no orders/UI/logging in the lock.
                bool own=Zero(old,0,512)||(U32(old,0)==AckMagic&&U32(old,8)==2&&Same(old,96,b,96,192));
                if(own){view.WriteArray(256+slot*512,b,0,512);assigned[key]=slot;}else assigned.Remove(key);
            }
            finally { if(locked)mutex.ReleaseMutex(); }
        }
        public void Dispose()
        {
            if(view!=null){view.Dispose();view=null;}if(file!=null){file.Dispose();file=null;}
            if(mutex!=null){mutex.Dispose();mutex=null;}assigned.Clear();ownerPrepared=false;
        }
    }

    // Per-account realized trade ledger. Execution quantity/price/commission are deduplicated before finalizing a ticket.
    internal sealed class OzRiskManager
    {
        internal sealed class Fill
        {
            internal DateTime WhenUtc; internal string Key; internal bool Entry; internal int Quantity; internal double Price, Commission, PointValue;
        }
        internal sealed class Ticket
        {
            internal ulong Id; internal string Side; internal bool Final; internal DateTime ClosedUtc;
            internal readonly Dictionary<string,Fill> Fills = new Dictionary<string,Fill>();
            internal int Entered { get { return Fills.Values.Where(f=>f.Entry).Sum(f=>f.Quantity); } }
            internal int Exited { get { return Fills.Values.Where(f=>!f.Entry).Sum(f=>f.Quantity); } }
            internal double Net
            {
                get
                {
                    double entry=Fills.Values.Where(f=>f.Entry).Sum(f=>f.Quantity*f.Price*f.PointValue);
                    double exit=Fills.Values.Where(f=>!f.Entry).Sum(f=>f.Quantity*f.Price*f.PointValue);
                    return (Side=="BUY"?exit-entry:entry-exit)-Fills.Values.Sum(f=>f.Commission);
                }
            }
        }
        internal readonly Dictionary<ulong,Ticket> Tickets=new Dictionary<ulong,Ticket>();
        internal bool DailyLocked, DataFault;
        internal double Baseline, Equity, Pnl;
        internal int DailyLosses, Consecutive;
        internal DateTime CooldownUntilUtc=DateTime.MinValue, StreakResetUtc=DateTime.MinValue;
        internal string Day="",Reason="ACCOUNT_DATA_NOT_READY";
        private readonly string path; private readonly Action<string> log;
        private OzAccountSettings settings;
        private bool dirty;
        private ulong saveTick;
        internal OzRiskManager(string accountKey,OzAccountSettings configuration,Action<string> logger)
        { path=Oz11.PathFor("risk",accountKey);settings=configuration;log=logger;Load(); }
        private string DayKey(DateTime utc)
        { return TimeZoneInfo.ConvertTimeFromUtc(utc,TimeZoneInfo.FindSystemTimeZoneById(settings.DayZone)).AddMinutes(-settings.DayStartMinutes).ToString("yyyyMMdd",Oz11.Inv); }
        internal void UpdateSettings(OzAccountSettings next)
        {
            next.Validate();
            if(Day!=""&&(settings.DayZone!=next.DayZone||settings.DayStartMinutes!=next.DayStartMinutes))
                throw new InvalidOperationException("DAILY_CLOCK_CHANGE_REQUIRES_RESTART_FLAT");
            settings=next; // Never resets today's lock, loss counters, or cooldown.
        }
        internal void OnFill(ulong id,string side,string key,bool entry,int quantity,double price,double commission,double pointValue,DateTime whenUtc)
        {
            if(whenUtc.Kind!=DateTimeKind.Utc||whenUtc.Year<2000||whenUtc>DateTime.UtcNow.AddMinutes(5)||id==0||(side!="BUY"&&side!="SELL")||string.IsNullOrEmpty(key)||quantity<=0||!Oz11.Finite(price)||price<=0||!Oz11.Finite(commission)||commission<0||!Oz11.Finite(pointValue)||pointValue<=0)
            { DataFault=true;Reason="INVALID_EXECUTION_DATA";return; }
            Ticket t;if(!Tickets.TryGetValue(id,out t)){t=new Ticket{Id=id,Side=side};Tickets.Add(id,t);}
            if(t.Side!=side){DataFault=true;Reason="EXECUTION_SIDE_CONFLICT";return;}
            Fill prior;
            if(t.Fills.TryGetValue(key,out prior))
            {
                if(prior.Entry!=entry||prior.Quantity!=quantity||prior.Price!=price||prior.PointValue!=pointValue||prior.WhenUtc!=whenUtc)
                { DataFault=true;Reason="EXECUTION_CORRECTION_REQUIRES_REVIEW";return; }
                if(prior.Commission!=commission){prior.Commission=commission;dirty=true;Recalculate();}
                return;
            }
            if(t.Final){DataFault=true;Reason="LATE_FILL_AFTER_FINALIZATION";return;}
            t.Fills[key]=new Fill{WhenUtc=whenUtc,Key=key,Entry=entry,Quantity=quantity,Price=price,Commission=commission,PointValue=pointValue};dirty=true;
        }
        internal bool UpdateKnownExecution(string key,int quantity,double price,double commission,double pointValue,DateTime whenUtc)
        {
            foreach(var ticket in Tickets.Values)
            {
                Fill prior;if(!ticket.Fills.TryGetValue(key,out prior))continue;
                OnFill(ticket.Id,ticket.Side,key,prior.Entry,quantity,price,commission,pointValue,whenUtc);
                return true;
            }
            return false; // unrelated manual executions do not become copied ticket results
        }
        internal bool HasExecution(string key)
        { return Tickets.Values.Any(t=>t.Fills.ContainsKey(key)); }
        internal bool CanFinalize(ulong id,int initial)
        { Ticket t;return Tickets.TryGetValue(id,out t)&&initial>0&&t.Entered==initial&&t.Exited==initial; }
        internal bool Finalize(ulong id,int initial)
        {
            if(!CanFinalize(id,initial))return false;
            Ticket t=Tickets[id];if(t.Final)return true;
            t.Final=true;t.ClosedUtc=t.Fills.Values.Where(f=>!f.Entry).Max(f=>f.WhenUtc);dirty=true;Recalculate();
            log("[TICKET_RESULT] MasterId="+id+" | Net="+t.Net.ToString("F2",Oz11.Inv)+" | DailyLosses="+DailyLosses+" | Consecutive="+Consecutive);
            Save(true);return !DataFault;
        }
        private void Recalculate()
        {
            DailyLosses=0;Consecutive=0;
            var completed=Tickets.Values.Where(t=>t.Final&&DayKey(t.ClosedUtc)==Day).OrderBy(t=>t.ClosedUtc).ThenBy(t=>t.Id).ToList();
            foreach(Ticket t in completed)
            {
                if(t.Net < -0.0000001)DailyLosses++;
                if(t.ClosedUtc<=StreakResetUtc)continue;
                if(t.Net < -0.0000001)Consecutive++;else Consecutive=0; // breakeven breaks streak
            }
            if(settings.DailyLossTrades>0&&DailyLosses>=settings.DailyLossTrades) { /* entry lock only, no forced liquidation */ }
            if(settings.ConsecutiveLosses>0&&Consecutive>=settings.ConsecutiveLosses&&completed.Count>0&&settings.CooldownMinutes>0)
            {
                DateTime until=completed.Last().ClosedUtc.AddMinutes(settings.CooldownMinutes);
                if(until>CooldownUntilUtc){CooldownUntilUtc=until;dirty=true;}
            }
        }
        internal void ObserveEquity(double equity,bool brokerOnline,bool exposure,DateTime now)
        {
            if(!brokerOnline||!Oz11.Finite(equity)||equity<=0){Reason="ACCOUNT_EQUITY_UNAVAILABLE";Equity=double.NaN;return;}
            Equity=equity;string today=DayKey(now);
            if(Day!=today)
            {
                if(DailyLocked&&exposure){Reason="DAILY_LIQUIDATION_PENDING";return;}
                Day=today;Baseline=equity;DailyLocked=false;StreakResetUtc=DateTime.MinValue;CooldownUntilUtc=DateTime.MinValue;dirty=true;Recalculate();Save(true);
            }
            if(Baseline<=0||!Oz11.Finite(Baseline)){DataFault=true;Reason="DAILY_BASELINE_INVALID";return;}
            Pnl=Equity-Baseline;
            if(!DailyLocked&&Pnl<=-Baseline*settings.DailyLossPercent/100.0)
            { DailyLocked=true;dirty=true;Save(true);log("[DAILY_LOCK] Account equity limit hit; entry disabled before liquidation requests."); }
            while(CooldownUntilUtc!=DateTime.MinValue&&now>=CooldownUntilUtc)
            {
                DateTime ended=CooldownUntilUtc;CooldownUntilUtc=DateTime.MinValue;
                if(ended>StreakResetUtc)StreakResetUtc=ended;
                dirty=true;Recalculate();Save(true);
            }
            Save(false);
        }
        internal bool EntryAllowed(int slots,DateTime now,out string reason)
        {
            reason="READY";
            if(DataFault)reason=Reason==""?"RISK_STATE_FAULT":Reason;
            else if(Day!=DayKey(now))reason="DAILY_ROLLOVER_PENDING";
            else if(!Oz11.Finite(Equity)||Equity<=0||Baseline<=0||Day=="")reason="ACCOUNT_EQUITY_UNAVAILABLE";
            else if(DailyLocked)reason="DAILY_LOCK";
            else if(settings.MaxPositions>0&&slots>=settings.MaxPositions)reason="MAX_POSITIONS";
            else if(settings.DailyLossTrades>0&&DailyLosses>=settings.DailyLossTrades)reason="DAILY_LOSS_COUNT";
            else if(now<CooldownUntilUtc)reason="COOLDOWN";
            else if(settings.ConsecutiveLosses>0&&Consecutive>=settings.ConsecutiveLosses&&settings.CooldownMinutes==0)reason="CONSECUTIVE_DAY_LOCK";
            return reason=="READY";
        }
        internal double CooldownLeft { get { return Math.Max(0,(CooldownUntilUtc-DateTime.UtcNow).TotalSeconds); } }
        private void Load()
        {
            Equity=double.NaN;
            if(!System.IO.File.Exists(path))return;
            try
            {
                var root=System.Xml.Linq.XDocument.Load(path).Root;
                if((string)root.Attribute("schema")!="2")throw new FormatException("RISK_SCHEMA");
                Day=(string)root.Attribute("day");if(string.IsNullOrEmpty(Day))throw new FormatException("RISK_DAY");Baseline=double.Parse((string)root.Attribute("baseline"),Oz11.Inv);
                if(!Oz11.Finite(Baseline)||Baseline<=0)throw new FormatException("RISK_BASELINE");
                DailyLocked=bool.Parse((string)root.Attribute("locked"));
                CooldownUntilUtc=DateTime.Parse((string)root.Attribute("cooldown"),Oz11.Inv,System.Globalization.DateTimeStyles.RoundtripKind);
                StreakResetUtc=DateTime.Parse((string)root.Attribute("reset"),Oz11.Inv,System.Globalization.DateTimeStyles.RoundtripKind);
                if((string)root.Attribute("zone")!=settings.DayZone||int.Parse((string)root.Attribute("start"),Oz11.Inv)!=settings.DayStartMinutes)
                    throw new FormatException("DAILY_CLOCK_MISMATCH");
                foreach(var e in root.Elements("ticket"))
                {
                    var t=new Ticket{Id=ulong.Parse((string)e.Attribute("id"),Oz11.Inv),Side=(string)e.Attribute("side"),Final=bool.Parse((string)e.Attribute("final")),
                        ClosedUtc=DateTime.Parse((string)e.Attribute("closed"),Oz11.Inv,System.Globalization.DateTimeStyles.RoundtripKind)};
                    foreach(var f in e.Elements("fill"))
                    {
                        var v=new Fill{WhenUtc=DateTime.Parse((string)f.Attribute("utc"),Oz11.Inv,System.Globalization.DateTimeStyles.RoundtripKind),Key=(string)f.Attribute("key"),Entry=bool.Parse((string)f.Attribute("entry")),Quantity=int.Parse((string)f.Attribute("qty"),Oz11.Inv),
                            Price=double.Parse((string)f.Attribute("price"),Oz11.Inv),Commission=double.Parse((string)f.Attribute("commission"),Oz11.Inv),PointValue=double.Parse((string)f.Attribute("point"),Oz11.Inv)};
                        if(v.WhenUtc.Kind!=DateTimeKind.Utc||v.WhenUtc.Year<2000||v.Quantity<=0||!Oz11.Finite(v.Price)||v.Price<=0||!Oz11.Finite(v.Commission)||v.Commission<0||!Oz11.Finite(v.PointValue)||v.PointValue<=0)throw new FormatException("RISK_FILL");
                        t.Fills.Add(v.Key,v);
                    }
                    if(t.Final&&(t.Entered<=0||t.Entered!=t.Exited))throw new FormatException("RISK_FINAL_QUANTITY");
                    Tickets.Add(t.Id,t);
                }
                Recalculate();
                if(Tickets.Values.Any(t=>!t.Final&&t.Entered>t.Exited))
                { DataFault=true;Reason="RESTART_WITH_OPEN_TICKET_LEDGER"; } // no invented position/order attribution
            }
            catch(Exception ex){DataFault=true;Reason="RISK_STATE_LOAD_FAILED";log("[RISK_STATE_LOAD_FAILED] "+ex.Message);}
        }
        internal void Save(bool force)
        {
            ulong now=OzMasterReader.Tick();if(!dirty||(!force&&now-saveTick<1000))return;
            try
            {
                var root=new System.Xml.Linq.XElement("risk",new System.Xml.Linq.XAttribute("schema","2"),new System.Xml.Linq.XAttribute("day",Day),
                    new System.Xml.Linq.XAttribute("baseline",Baseline.ToString("R",Oz11.Inv)),new System.Xml.Linq.XAttribute("locked",DailyLocked),
                    new System.Xml.Linq.XAttribute("cooldown",CooldownUntilUtc.ToString("O",Oz11.Inv)),new System.Xml.Linq.XAttribute("reset",StreakResetUtc.ToString("O",Oz11.Inv)),
                    new System.Xml.Linq.XAttribute("zone",settings.DayZone),new System.Xml.Linq.XAttribute("start",settings.DayStartMinutes));
                foreach(Ticket t in Tickets.Values)
                {
                    var e=new System.Xml.Linq.XElement("ticket",new System.Xml.Linq.XAttribute("id",t.Id),new System.Xml.Linq.XAttribute("side",t.Side),
                        new System.Xml.Linq.XAttribute("final",t.Final),new System.Xml.Linq.XAttribute("closed",t.ClosedUtc.ToString("O",Oz11.Inv)));
                    foreach(Fill f in t.Fills.Values)e.Add(new System.Xml.Linq.XElement("fill",new System.Xml.Linq.XAttribute("utc",f.WhenUtc.ToString("O",Oz11.Inv)),new System.Xml.Linq.XAttribute("key",f.Key),new System.Xml.Linq.XAttribute("entry",f.Entry),
                        new System.Xml.Linq.XAttribute("qty",f.Quantity),new System.Xml.Linq.XAttribute("price",f.Price.ToString("R",Oz11.Inv)),
                        new System.Xml.Linq.XAttribute("commission",f.Commission.ToString("R",Oz11.Inv)),new System.Xml.Linq.XAttribute("point",f.PointValue.ToString("R",Oz11.Inv))));
                    root.Add(e);
                }
                Oz11.SaveXml(path,new System.Xml.Linq.XDocument(root));dirty=false;saveTick=now;
            }
            catch(Exception ex){DataFault=true;Reason="RISK_STATE_SAVE_FAILED";log("[RISK_STATE_SAVE_FAILED] "+ex.Message);}
        }
    }

    internal sealed class OzMasterReader : IDisposable
    {
        internal const int Capacity = 4096, Batch = 16, SlotBytes = 1024;
        internal const long EventsBytes = 4194560, StateBytes = 65536;
        internal const ulong MaxSequence = 9223372036854775807;
        [System.Runtime.InteropServices.DllImport("kernel32.dll")]
        private static extern ulong GetTickCount64();
        internal static ulong Tick() { return GetTickCount64(); }
        internal sealed class Command
        {
            internal string Action, Symbol;
            internal ulong Ticket;
            internal double Price, Sl, Tp, SlDist, TargetVolume, ClosedVolume;
        }
        internal sealed class Envelope
        {
            internal string Session, Payload;
            internal ulong Sequence, PublishedTick;
            internal Command Trade;
        }
        internal sealed class Snapshot
        {
            internal FullState Full;
            internal string Session, Server, Payload;
            internal ulong Login, Published, Alive, Version, Base, StateTick;
        }
        internal sealed class Pulse
        {
            internal Snapshot State;
            internal List<Envelope> Commands = new List<Envelope>();
            internal OzCommState Link;
            internal string Reason;
            internal bool Reset;
            internal long Epoch;
        }
        private readonly string stream, expectedServer, pinPath;
        private readonly ulong expectedLogin;
        private ulong login, cursor;
        private string sourceServer="",session="";
        private bool bound, needReset=true;
        private long epoch;
        private ulong lastPoll;
        private int probe;
        private Snapshot latest;
        private OzCommState link=OzCommState.TEMP_DISCONNECTED;
        private string reason="WAITING_FOR_MASTER";
        private readonly byte[] header=new byte[256],stateHeader=new byte[128],stateBody=new byte[65408];
        private readonly byte[][] slots=Enumerable.Range(0,Batch).Select(i=>new byte[SlotBytes]).ToArray();
        private MemoryMappedFile eventsFile,stateFile;
        private MemoryMappedViewAccessor eventsView,stateView;
        private Mutex copyLock;
        private readonly UTF8Encoding utf8=new UTF8Encoding(false,true);
        internal OzMasterReader(string id,ulong expected,string server)
        {
            if(string.IsNullOrEmpty(id)||id.Length>32||id.Any(c=>!(c>='A'&&c<='Z')&&!(c>='0'&&c<='9')&&c!='_'))throw new ArgumentException("INVALID_STREAM");
            stream=id;expectedLogin=expected;expectedServer=server??"";pinPath=Oz11.PathFor("source",stream);
            if(System.IO.File.Exists(pinPath))
            {
                var p=System.Xml.Linq.XDocument.Load(pinPath).Root;
                login=ulong.Parse((string)p.Attribute("login"),Oz11.Inv);sourceServer=(string)p.Attribute("server");
                if(login==0||string.IsNullOrEmpty(sourceServer)||(expectedLogin!=0&&login!=expectedLogin)||(expectedServer!=""&&sourceServer!=expectedServer))
                    throw new InvalidOperationException("SOURCE_PIN_MISMATCH");
            }
        }
        internal void RequestProbe() { Interlocked.Exchange(ref probe,1); }
        private void Open()
        {
            if(eventsView!=null&&stateView!=null&&copyLock!=null)return;
            string root="Local\\OZCopy.v2."+stream;
            try
            {
                eventsFile=MemoryMappedFile.OpenExisting(root+".Events",MemoryMappedFileRights.Read);
                stateFile=MemoryMappedFile.OpenExisting(root+".State",MemoryMappedFileRights.Read);
                copyLock=Mutex.OpenExisting(root+".CopyLock",System.Security.AccessControl.MutexRights.Synchronize|System.Security.AccessControl.MutexRights.Modify);
                eventsView=eventsFile.CreateViewAccessor(0,EventsBytes,MemoryMappedFileAccess.Read);
                stateView=stateFile.CreateViewAccessor(0,StateBytes,MemoryMappedFileAccess.Read);
            }
            catch{Dispose();throw;}
        }
        public void Dispose()
        {
            if(eventsView!=null){eventsView.Dispose();eventsView=null;}if(stateView!=null){stateView.Dispose();stateView=null;}
            if(eventsFile!=null){eventsFile.Dispose();eventsFile=null;}if(stateFile!=null){stateFile.Dispose();stateFile=null;}
            if(copyLock!=null){copyLock.Dispose();copyLock=null;}
        }
        private Pulse Failure(OzCommState state,string why)
        {
            if(link!=state)Oz11.Burst(state==OzCommState.HARD_HOLD?"MMF RECOVERY REQUIRED":"MMF TEMP DISCONNECTED",why,"NINJA READER "+stream);
            link=state;reason=why;needReset=true;
            return new Pulse{State=latest,Link=link,Reason=reason,Epoch=epoch};
        }
        internal Pulse Poll()
        {
            ulong now=Tick();
            if(lastPoll>0&&!Oz11.Fresh(now,lastPoll))needReset=true;lastPoll=now;
            if(Interlocked.Exchange(ref probe,0)!=0&&link!=OzCommState.RUNNING){Dispose();needReset=true;}
            int count=0;bool owns=false,headerOk=false,stateOk=false;
            try
            {
                Open();
                try
                {
                    try{owns=copyLock.WaitOne(0);}catch(AbandonedMutexException){owns=true;}
                    if(!owns)
                    {
                        if(latest==null||!Oz11.Fresh(now,latest.Alive))return Failure(OzCommState.TEMP_DISCONNECTED,"HEARTBEAT_EXPIRED");
                        return new Pulse{State=latest,Link=link,Reason=reason,Epoch=epoch};
                    }
                    eventsView.ReadArray(0,header,0,256);headerOk=HeaderValid(header);
                    if(headerOk&&U32(header,64)==1)
                    {
                        stateView.ReadArray(0,stateHeader,0,128);stateOk=StateHeaderValid(stateHeader,header);
                        if(stateOk)stateView.ReadArray(128,stateBody,0,65408);
                        ulong w=U64(header,48);
                        if(bound&&!needReset&&w>=cursor&&w-cursor<=Capacity)
                        {
                            count=(int)Math.Min((ulong)Batch,w-cursor);
                            for(int i=0;i<count;i++)eventsView.ReadArray(256+(long)((cursor+(ulong)i)%Capacity)*SlotBytes,slots[i],0,SlotBytes);
                        }
                    }
                }
                finally{if(owns){copyLock.ReleaseMutex();owns=false;}}
                if(!headerOk)return Failure(OzCommState.HARD_HOLD,"EVENT_HEADER_CRC_OR_LAYOUT");
                string id=Hex(header,32),server=Decode(header,88,(int)U32(header,84));ulong source=U64(header,72);
                if((expectedLogin!=0&&source!=expectedLogin)||(expectedServer!=""&&server!=expectedServer)||(login!=0&&(source!=login||server!=sourceServer)))
                    return Failure(OzCommState.HARD_HOLD,"SOURCE_ACCOUNT_CHANGED");
                if(U32(header,64)!=1)return Failure(OzCommState.TEMP_DISCONNECTED,"PUBLISHER_NOT_READY");
                var next=new Snapshot{Session=id,Server=server,Login=source,Published=U64(header,48),Alive=U64(header,56),
                    Version=U64(stateHeader,40),Base=U64(stateHeader,48),StateTick=U64(stateHeader,56)};
                if(!Oz11.Fresh(Tick(),next.Alive))return Failure(OzCommState.TEMP_DISCONNECTED,"HEARTBEAT_EXPIRED");
                if(!stateOk)return Failure(OzCommState.HARD_HOLD,"STATE_HEADER_CRC_OR_LAYOUT");
                int length=(int)U32(stateHeader,20);
                if(U32(stateHeader,64)!=Crc(stateBody,0,length)||!Zero(stateBody,length,65408-length))return Failure(OzCommState.HARD_HOLD,"STATE_PAYLOAD_CRC");
                if(U16(stateHeader,72)!=2)return Failure(OzCommState.HARD_HOLD,"RECOVERY_REQUIRES_FULL_SNAPSHOT");
                next.Full=ParseFull(Decode(stateBody,0,length),next.Base);next.Payload=next.Full.Legacy();
                bool changed=bound&&id!=session;
                if(changed)needReset=true;
                if(bound&&!changed&&(next.Published<cursor||next.Published-cursor>Capacity))
                {
                    if(!needReset)Oz11.Burst("MMF TEMP DISCONNECTED",next.Published<cursor?"SEQUENCE_REGRESSION":"RING_GAP","NINJA READER "+stream);
                    needReset=true;
                }
                if(bound&&!changed&&!needReset&&latest!=null&&(next.Version<latest.Version||next.Full.Revision<latest.Full.Revision))
                    return Failure(OzCommState.HARD_HOLD,"STATE_VERSION_REGRESSION");
                bool reset=needReset||!bound;
                if(reset)
                {
                    if(next.Base!=next.Published||!next.Full.Consistent||!Oz11.Fresh(Tick(),next.StateTick))
                        return Failure(OzCommState.TEMP_DISCONNECTED,"WAITING_FOR_CURRENT_CONSISTENT_SNAPSHOT");
                    if(login==0&&next.Full.Rows.Count!=0)return Failure(OzCommState.HARD_HOLD,"FIRST_BIND_REQUIRES_SOURCE_FLAT");
                    if(login==0)
                    {
                        Oz11.SaveXml(pinPath,new System.Xml.Linq.XDocument(new System.Xml.Linq.XElement("source",
                            new System.Xml.Linq.XAttribute("login",source),new System.Xml.Linq.XAttribute("server",server))));
                    }
                    login=source;sourceServer=server;session=id;cursor=next.Published;bound=true;needReset=false;epoch++;
                    count=0; // no replay across outage/session boundary
                }
                var commands=new List<Envelope>();
                if(!reset)
                {
                    for(int i=0;i<count;i++)commands.Add(Frame(slots[i],id,cursor+(ulong)i+1));
                    cursor+=(ulong)commands.Count;
                }
                latest=next;link=OzCommState.RUNNING;reason="CONNECTED";
                return new Pulse{State=next,Commands=commands,Link=link,Reason=reason,Reset=reset,Epoch=epoch};
            }
            catch(System.IO.FileNotFoundException){Dispose();return Failure(OzCommState.TEMP_DISCONNECTED,"MMF_DISCONNECTED");}
            catch(WaitHandleCannotBeOpenedException){Dispose();return Failure(OzCommState.TEMP_DISCONNECTED,"COPY_LOCK_MISSING");}
            catch(FormatException ex){return Failure(OzCommState.HARD_HOLD,ex.Message);}
            catch(Exception ex){Dispose();return Failure(OzCommState.HARD_HOLD,"READER_EXCEPTION:"+ex.GetType().Name);}
        }

        internal static uint U16(byte[] b, int p) { return (uint)b[p] | ((uint)b[p + 1] << 8); }
        internal static uint U32(byte[] b, int p) { uint v = 0; for (int i = 0; i < 4; i++) v |= (uint)b[p + i] << (8 * i); return v; }
        internal static ulong U64(byte[] b, int p) { ulong v = 0; for (int i = 0; i < 8; i++) v |= (ulong)b[p + i] << (8 * i); return v; }
        internal static uint Crc(byte[] b, int p, int n, int zero = -1)
        {
            uint crc = 0xFFFFFFFF;
            for (int i = p; i < p + n; i++)
            {
                crc ^= (zero >= 0 && i >= zero && i < zero + 4) ? 0U : b[i];
                for (int bit = 0; bit < 8; bit++) crc = (crc & 1) != 0 ? (crc >> 1) ^ 0xEDB88320U : crc >> 1;
            }
            return crc ^ 0xFFFFFFFF;
        }
        private static bool Zero(byte[] b, int p, int n) { for (int i = p; i < p + n; i++) if (b[i] != 0) return false; return true; }
        private static bool Same(byte[] a, int p, byte[] b, int q, int n) { for (int i = 0; i < n; i++) if (a[p + i] != b[q + i]) return false; return true; }
        private static bool Magic(byte[] b, char suffix)
        { return b[0] == 'O' && b[1] == 'Z' && b[2] == 'M' && b[3] == 'M' && b[4] == 'F' && b[5] == suffix && b[6] == '2' && b[7] == 0; }
        internal static bool HeaderValid(byte[] b)
        {
            if (b.Length != 256 || !Magic(b, 'R') || U16(b, 8) != 2 || U16(b, 10) != 0 || U32(b, 12) != 256 || U32(b, 16) != 1024 || U32(b, 20) != 4096 || U64(b, 24) != EventsBytes) return false;
            if (Zero(b, 32, 16) || U64(b, 48) > MaxSequence || U32(b, 64) > 3 || U32(b, 68) != 0 || U64(b, 72) == 0) return false;
            uint n = U32(b, 84);
            return n >= 1 && n <= 96 && Zero(b, 88 + (int)n, 168 - (int)n) && U32(b, 80) == Crc(b, 0, 256, 80);
        }
        internal static bool StateHeaderValid(byte[] s, byte[] h)
        {
            if (s.Length != 128 || !Magic(s, 'S') || U16(s, 8) != 2 || U16(s, 10) != 0 || U32(s, 12) != 128 || U32(s, 16) != 65536) return false;
            uint n = U32(s, 20);
            return n >= 1 && n <= 65408 && Same(s, 24, h, 32, 16) && U64(s, 40) > 0 && U64(s, 48) <= U64(h, 48) &&
                (U16(s, 72) == 1 || U16(s, 72) == 2) && U16(s, 74) == 1 && U32(s, 76) == 1 && Zero(s, 80, 48) && U32(s, 68) == Crc(s, 0, 128, 68);
        }
        private string Decode(byte[] b, int p, int n)
        {
            string s = utf8.GetString(b, p, n);
            if (s.Length == 0 || s.IndexOfAny(new[] { '\0', '\r', '\n', '\uFEFF' }) >= 0) throw new FormatException("UTF8_PAYLOAD");
            return s;
        }
        private static string Hex(byte[] b, int p) { return BitConverter.ToString(b, p, 16).Replace("-", ""); }
        private static double Number(string s, bool positive)
        {
            if (string.IsNullOrEmpty(s)) throw new FormatException("EMPTY_NUMBER");
            bool dot = false;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c == '.') { if (dot || i == 0 || i == s.Length - 1) throw new FormatException("NUMBER_DOT"); dot = true; }
                else if (c < '0' || c > '9') throw new FormatException("NUMBER_CHARACTER");
            }
            double value;
            if (!double.TryParse(s, System.Globalization.NumberStyles.AllowDecimalPoint, System.Globalization.CultureInfo.InvariantCulture, out value) || double.IsNaN(value) || double.IsInfinity(value) || (positive ? value <= 0 : value < 0)) throw new FormatException("NUMBER_RANGE");
            return value;
        }
        internal sealed class FullRow
        {
            internal ulong Id, CurrentTicket, EntrySequence, LastSequence;
            internal string Symbol, Side;
            internal double InitialVolume, Volume, Price, Sl, Tp, Step, CopyPrice, CopyDistance;
            internal int Status;
        }
        internal sealed class FullState
        {
            internal ulong Revision;
            internal bool Consistent;
            internal string Reason;
            internal List<FullRow> Rows = new List<FullRow>();
            internal string Legacy()
            {
                if (Rows.Count == 0) return "ACTION=SYNC|TICKETS=NONE";
                return "ACTION=SYNC|TICKETS=" + string.Join(",", Rows.Select(r =>
                    r.Id.ToString(System.Globalization.CultureInfo.InvariantCulture) + ":" +
                    r.Sl.ToString("F8", System.Globalization.CultureInfo.InvariantCulture) + ":" +
                    r.Tp.ToString("F8", System.Globalization.CultureInfo.InvariantCulture)));
            }
        }
        private static ulong Unsigned(string text)
        {
            ulong value;
            if (string.IsNullOrEmpty(text) || text.Length > 20 || text.Any(c => c < '0' || c > '9') ||
                !ulong.TryParse(text, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out value)) throw new FormatException("SNAPSHOT_INTEGER");
            return value;
        }
        private static string SymbolFromHex(string hex)
        {
            if (hex.Length < 2 || hex.Length > 512 || hex.Length % 2 != 0 || hex.Any(c => !(c >= '0' && c <= '9') && !(c >= 'A' && c <= 'F'))) throw new FormatException("SYMBOL_HEX");
            byte[] b = new byte[hex.Length / 2];
            for (int i = 0; i < b.Length; i++) b[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
            string value = new UTF8Encoding(false, true).GetString(b);
            if (value.Length == 0 || value.IndexOfAny(new[] { '\0', '\r', '\n', '\uFEFF' }) >= 0) throw new FormatException("SYMBOL_UTF8");
            return value;
        }
        internal static FullState ParseFull(string payload, ulong boundary)
        {
            string[] p = payload.Split('|');
            string[] keys = { "ACTION=SNAPSHOT", "SCHEMA=1", "CUT=", "REV=", "CONSISTENT=", "REASON=", "COUNT=", "ROWS=" };
            if (p.Length != 8 || p[0] != keys[0] || p[1] != keys[1]) throw new FormatException("SNAPSHOT_FIELDS");
            for (int i = 2; i < 8; i++) if (!p[i].StartsWith(keys[i], StringComparison.Ordinal)) throw new FormatException("SNAPSHOT_KEY_ORDER");
            if (Unsigned(p[2].Substring(4)) != boundary) throw new FormatException("SNAPSHOT_CUT");
            var result = new FullState { Revision = Unsigned(p[3].Substring(4)), Reason = p[5].Substring(7) };
            if (p[4] != "CONSISTENT=0" && p[4] != "CONSISTENT=1") throw new FormatException("SNAPSHOT_CONSISTENCY");
            result.Consistent = p[4] == "CONSISTENT=1";
            if (!new[] { "OK", "PENDING_EVENTS", "CHANGING", "UNSUPPORTED", "HISTORY_UNAVAILABLE", "SIGNAL_STATE_MISMATCH" }.Contains(result.Reason) || result.Consistent != (result.Reason == "OK")) throw new FormatException("SNAPSHOT_REASON");
            ulong count = Unsigned(p[6].Substring(6)); string list = p[7].Substring(5);
            if (count > 1024 || (count == 0 && list != "NONE")) throw new FormatException("SNAPSHOT_COUNT");
            if (count == 0) return result;
            string[] entries = list.Split(';');
            if ((ulong)entries.Length != count) throw new FormatException("SNAPSHOT_ROWS");
            ulong previous = 0;
            foreach (string entry in entries)
            {
                string[] f = entry.Split(',');
                if (f.Length != 15) throw new FormatException("SNAPSHOT_ROW_FIELDS");
                var r = new FullRow {
                    Id = Ticket(f[0]), CurrentTicket = Ticket(f[1]), Symbol = SymbolFromHex(f[2]), Side = f[3],
                    InitialVolume = Number(f[4], true), Volume = Number(f[5], true), Price = Number(f[6], true),
                    Sl = Number(f[7], false), Tp = Number(f[8], false), Step = Number(f[9], true),
                    EntrySequence = Unsigned(f[10]), LastSequence = Unsigned(f[11]),
                    CopyPrice = Number(f[12], false), CopyDistance = Number(f[13], false)
                };
                ulong status = Unsigned(f[14]);
                if (r.Id <= previous || (r.Side != "BUY" && r.Side != "SELL") || status > 3 || r.EntrySequence > r.LastSequence || r.LastSequence > boundary) throw new FormatException("SNAPSHOT_ROW_ID");
                r.Status = (int)status;
                if (status == 2) { if (r.EntrySequence == 0 || r.CopyPrice <= 0 || r.CopyDistance <= 0) throw new FormatException("SNAPSHOT_COPY_REFERENCE"); }
                else if (r.EntrySequence != 0 || r.CopyPrice != 0 || r.CopyDistance != 0) throw new FormatException("SNAPSHOT_UNPUBLISHED_REFERENCE");
                if (result.Consistent && (status == 3 || r.Volume > r.InitialVolume + r.Step * 0.0001)) throw new FormatException("SNAPSHOT_VOLUME");
                result.Rows.Add(r); previous = r.Id;
            }
            return result;
        }

        private static ulong Ticket(string s)
        {
            ulong n;
            if (string.IsNullOrEmpty(s) || s.Any(c => c < '0' || c > '9') || !ulong.TryParse(s, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out n) || n == 0) throw new FormatException("TICKET");
            return n;
        }
        internal static Command ParseCommand(string payload)
        {
            var fields = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (string part in payload.Split('|'))
            {
                int eq = part.IndexOf('=');
                if (eq <= 0 || eq == part.Length - 1 || part.IndexOf('=', eq + 1) >= 0 || part.IndexOfAny(new[] { '\0', '\r', '\n', '\uFEFF' }) >= 0) throw new FormatException("COMMAND_FIELD");
                string key = part.Substring(0, eq); if (fields.ContainsKey(key)) throw new FormatException("DUPLICATE_KEY"); fields.Add(key, part.Substring(eq + 1));
            }
            string action;
            if (!fields.TryGetValue("ACTION", out action)) throw new FormatException("ACTION");
            string[] required;
            if (action == "BUY" || action == "SELL") required = new[] { "ACTION", "SYMBOL", "PRICE", "SL", "TP", "SL_DIST", "TICKET" };
            else if (action == "MODIFY") required = new[] { "ACTION", "SYMBOL", "TICKET", "SL", "TP", "SL_DIST" };
            else if (action == "CLOSE") required = new[] { "ACTION", "SYMBOL", "TICKET", "TARGET_VOL", "CLOSED_VOL" };
            else throw new FormatException("ACTION");
            if (fields.Count != required.Length || required.Any(k => !fields.ContainsKey(k))) throw new FormatException("REQUIRED_FIELDS");
            var c = new Command { Action = action, Symbol = fields["SYMBOL"], Ticket = Ticket(fields["TICKET"]) };
            if (action == "CLOSE") { c.TargetVolume = Number(fields["TARGET_VOL"], false); c.ClosedVolume = Number(fields["CLOSED_VOL"], true); }
            else
            {
                bool entry = action != "MODIFY";
                c.Sl = Number(fields["SL"], entry); c.Tp = Number(fields["TP"], false); c.SlDist = Number(fields["SL_DIST"], entry);
                if (entry) c.Price = Number(fields["PRICE"], true);
            }
            return c;
        }
        internal static void ValidateSync(string payload)
        {
            const string prefix = "ACTION=SYNC|TICKETS=";
            if (!payload.StartsWith(prefix, StringComparison.Ordinal)) throw new FormatException("SYNC_PREFIX");
            string text = payload.Substring(prefix.Length); if (text == "NONE") return;
            foreach (string row in text.Split(',')) { string[] f = row.Split(':'); if (f.Length != 3) throw new FormatException("SYNC_ROW"); Ticket(f[0]); Number(f[1], false); Number(f[2], false); }
        }
        internal Envelope Frame(byte[] b, string id, ulong seq)
        {
            int n = (int)U32(b, 4);
            if (b.Length != 1024 || U32(b, 0) != 2 || n < 1 || n > 960 || U64(b, 8) != seq || Hex(b, 16) != id) throw new FormatException("FRAME_ID");
            if (U16(b, 40) != 1 || U16(b, 42) != 1 || U32(b, 44) != 0 || !Zero(b, 56, 8) || !Zero(b, 64 + n, 960 - n) || U32(b, 52) != Crc(b, 4, 48) || U32(b, 48) != Crc(b, 64, n)) throw new FormatException("FRAME_CRC_OR_FLAGS");
            string payload = Decode(b, 64, n);
            return new Envelope { Session = id, Sequence = seq, PublishedTick = U64(b, 32), Payload = payload, Trade = ParseCommand(payload) };
        }
// R11 shared reader polling defined above
    }

    internal sealed class OzAccountEngine
    {

        private EventWaitHandle r8AccountLease;
        private string r8LeasedAccount;
        private bool R8TryAccountLease(string accountName, out EventWaitHandle lease)
        {
            lease = null;
            try
            {
                string normalized = accountName.Trim().ToUpperInvariant();
                string hash;
                using (var sha = System.Security.Cryptography.SHA256.Create())
                    hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(normalized))).Replace("-", "");
                bool created;
                lease = new EventWaitHandle(false, EventResetMode.ManualReset,
                    @"Local\OZCopy.Receiver8.Account." + hash, out created);
                if (created) return true; // 커널 객체의 존재만 사용한다. UI/매매 스레드는 기다리지 않는다.
                lease.Dispose(); lease = null;
                PrintLog($"[ACCOUNT_ALREADY_OWNED] {accountName} | 같은 계좌의 다른 수신창이 연결되어 있습니다.");
            }
            catch (Exception ex)
            {
                if (lease != null) lease.Dispose();
                lease = null;
                PrintLog("[ACCOUNT_LEASE_FAILED] " + ex.Message);
            }
            return false;
        }
        private void R8ReleaseAccountLease()
        {
            EventWaitHandle lease = r8AccountLease;
            r8AccountLease = null; r8LeasedAccount = null;
            if (lease != null) lease.Dispose();
        }

        private sealed class R6Local
        {
            internal ulong EntrySequence, LastSequence;
            internal string Symbol, Side;
            internal bool Attempted, NoOpen, LocalExit, CloseSeen, Adopted;
            internal int CloseCount, ExpectedContracts = -1;
            internal double SourceRemaining = -1, SourceInitial = 0;
        }
        private readonly Dictionary<ulong, R6Local> r6Local = new Dictionary<ulong, R6Local>();
        private ulong r6Reported;
        private R6Local R6Record(ulong id)
        {
            R6Local r;
            if (!r6Local.TryGetValue(id, out r))
            {
                if (r6Local.Count >= 65536) throw new InvalidOperationException("LOCAL_AUDIT_CAPACITY");
                r = new R6Local(); r6Local.Add(id, r);
            }
            return r;
        }
        private void R6Before(OzMasterReader.Envelope item)
        {
            var c = item.Trade; R6Local r = R6Record(c.Ticket); r.LastSequence = item.Sequence;
            if (c.Action == "BUY" || c.Action == "SELL") { r.EntrySequence = item.Sequence; r.Symbol = c.Symbol; r.Side = c.Action; }
            if (c.Action == "CLOSE") { r.CloseSeen = true; r.CloseCount++; r.SourceRemaining = c.TargetVolume; }
        }
        private void R6After(OzMasterReader.Envelope item)
        {
            if (item.Trade.Action == "BUY" || item.Trade.Action == "SELL") R6Record(item.Trade.Ticket).NoOpen = !IsMasterTicketAlreadyActive(item.Trade.Ticket);
        }
        private void R6PlanClose(ulong id, int quantity)
        {
            R6Record(id).ExpectedContracts = quantity;
        }
        private readonly Dictionary<string,string> comparisonLog12=new Dictionary<string,string>();
        private readonly HashSet<string> netComparisonKeys12=new HashSet<string>();
        private void SnapshotLog12(string key,string state,string message)
        {
            string previous;if(comparisonLog12.TryGetValue(key,out previous)&&previous==state)return;
            if(comparisonLog12.Count>=65536&&!comparisonLog12.ContainsKey(key))return;
            comparisonLog12[key]=state;if(key.StartsWith("net:",StringComparison.Ordinal))netComparisonKeys12.Add(key);PrintLog(message);
        }
        private bool R6Reconcile(OzMasterReader.Snapshot snapshot)
        {
            var full = snapshot.Full;
            if (full == null) return true;
            bool report = r6Reported != snapshot.Version; r6Reported = snapshot.Version;
            if (!full.Consistent)
            {
                if (report) SnapshotLog12("snapshot",full.Reason,$"[SNAPSHOT_DEFERRED] Version={snapshot.Version} | Reason={full.Reason}");
                return false;
            }
            comparisonLog12["snapshot"]="OK";
            bool ok = true;
            List<CopyPositionMap> maps;
            lock (ticketMappings) maps = ticketMappings.Values.Where(m => m != null).ToList();
            foreach (var row in full.Rows)
            {
                R6Local local; r6Local.TryGetValue(row.Id, out local);
                var map = maps.FirstOrDefault(m => m.MasterTicket == row.Id);
                string status = "MATCH";
                if (row.Status != 2 && !(local != null && local.Adopted))
                {
                    if (map != null) { status = "UNPUBLISHED_SOURCE_MAPPING"; ok = false; }
                    else status = row.Status == 1 ? "SOURCE_PENDING_ENTRY" : "SOURCE_BASELINE";
                }
                else if (local == null || local.EntrySequence != row.EntrySequence || local.LastSequence != row.LastSequence) { status = "SIGNAL_HISTORY_MISMATCH"; ok = false; }
                else if (local.Symbol != row.Symbol || local.Side != row.Side) { status = "SOURCE_IDENTITY_MISMATCH"; ok = false; }
                else if (local.CloseSeen && Math.Abs(local.SourceRemaining - row.Volume) > row.Step * 0.0001) { status = "SOURCE_VOLUME_MISMATCH"; ok = false; }
                else if (map == null || map.CurrentContracts <= 0)
                {
                    if (local.LocalExit) status = "LOCAL_EXIT";
                    else if (local.NoOpen) status = local.Attempted ? "ENTRY_NOT_OPENED" : "ENTRY_NOT_SUBMITTED";
                    else { status = "MISSING_LOCAL_POSITION"; ok = false; }
                }
                else
                {
                    string mappedSymbol = MapSymbol(row.Symbol, settings.Mnq, settings.Mgc);
                    if (map.QuantityFault || map.Direction != row.Side || map.NinjaSymbol != mappedSymbol || (row.Status == 2 && Math.Abs(map.MasterEntryPrice - row.CopyPrice) > 0.0000001)) { status = "LOCAL_IDENTITY_OR_QUANTITY_FAULT"; ok = false; }
                    else if (local.LocalExit) status = "LOCAL_EXIT";
                    else if (local.ExpectedContracts >= 0 && map.CurrentContracts != local.ExpectedContracts) { status = "CLOSE_TARGET_MISMATCH"; ok = false; }
                    else
                    {
                        double ideal = map.InitialContracts * row.Volume / row.InitialVolume;
                        if (map.InitialContracts <= 0 || map.CurrentContracts < ideal - 1e-8 || map.CurrentContracts > ideal + local.CloseCount + 1e-8) { status = "VOLUME_RATIO_MISMATCH"; ok = false; }
                    }
                    if (status == "MATCH")
                    {
                        Instrument inst = Instrument.GetInstrument(map.NinjaSymbol);
                        if (inst != null)
                        {
                            double tick = Math.Max(inst.MasterInstrument.TickSize, 0.0000001);
                            double sl = inst.MasterInstrument.RoundToTickSize(map.AverageFillPrice + row.Sl - map.MasterEntryPrice);
                            double tp = row.Tp > 0 ? Math.Max(0, inst.MasterInstrument.RoundToTickSize(map.AverageFillPrice + row.Tp - map.MasterEntryPrice)) : 0;
                            bool slDiff = row.Sl > 0 && (map.SlOrder == null || IsProtectionOrderTerminal(map.SlOrder.OrderState) || Math.Abs(map.SlOrder.StopPrice - sl) >= tick * 0.5 || map.SlOrder.Quantity - map.SlOrder.Filled != map.CurrentContracts);
                            bool tpDiff = tp > 0 ? (map.TpOrder == null || IsProtectionOrderTerminal(map.TpOrder.OrderState) || Math.Abs(map.TpOrder.LimitPrice - tp) >= tick * 0.5 || map.TpOrder.Quantity - map.TpOrder.Filled != map.CurrentContracts) : (map.TpOrder != null && !IsProtectionOrderTerminal(map.TpOrder.OrderState));
                            if (slDiff || tpDiff) status = "PROTECTION_DIFFERENCE";
                        }
                    }
                }
                if (report) SnapshotLog12("ticket:"+row.Id,status,$"[SNAPSHOT_COMPARE] Version={snapshot.Version} | MasterId={row.Id} | {status} | SourceInitial={row.InitialVolume} | SourceRemaining={row.Volume} | LocalContracts={(map == null ? 0 : map.CurrentContracts)}");
            }
            foreach (var map in maps)
            {
                if (map.CurrentContracts > 0 && !full.Rows.Any(r => r.Id == map.MasterTicket))
                {
                    ok = false;
                    if (report) SnapshotLog12("ticket:"+map.MasterTicket,"SOURCE_CLOSED_LOCAL_REMAINS",$"[SNAPSHOT_COMPARE] SOURCE_CLOSED_LOCAL_REMAINS | MasterId={map.MasterTicket}");
                }
            }
            // 티켓 합계와 실제 계좌 net position을 별도로 대조한다. 다른 티켓/수동 물량을 배분하지 않는다.
            foreach (var group in maps.Where(m => m.CurrentContracts > 0).GroupBy(m => m.NinjaSymbol))
            {
                int expected = group.Sum(m => m.CurrentContracts);
                bool sameDirection = group.Select(m => m.Direction).Distinct().Count() == 1;
                Position position;
                lock (targetAccount.Positions) position = PositionSnapshot().FirstOrDefault(p => p.Instrument.FullName == group.Key);
                MarketPosition direction = group.First().Direction == "BUY" ? MarketPosition.Long : MarketPosition.Short;
                if (!sameDirection || position == null || position.Quantity != expected || position.MarketPosition != direction)
                {
                    ok = false;
                    if (report) SnapshotLog12("net:"+group.Key,"NET_QUANTITY_MISMATCH",$"[SNAPSHOT_COMPARE] NET_QUANTITY_MISMATCH | Symbol={group.Key} | Tracked={expected} | Actual={(position == null ? 0 : position.Quantity)}");
                }
            }
            List<Position> actual;
            lock (targetAccount.Positions) actual = targetAccount.Positions.Where(p => p.Quantity > 0 && p.MarketPosition != MarketPosition.Flat).ToList();
            foreach (var position in actual)
            {
                if (!maps.Any(m => m.CurrentContracts > 0 && m.NinjaSymbol == position.Instrument.FullName))
                {
                    ok = false;
                    if (report) SnapshotLog12("net:"+position.Instrument.FullName,"UNMAPPED_LOCAL_POSITION",$"[SNAPSHOT_COMPARE] UNMAPPED_LOCAL_POSITION | Symbol={position.Instrument.FullName} | Actual={position.Quantity}");
                }
            }
            if(ok){foreach(string key in netComparisonKeys12)comparisonLog12.Remove(key);netComparisonKeys12.Clear();}
            // 불일치가 있으면 기존 SYNC의 자동 고아청산/보호수정에도 넘기지 않는다.
            return ok;
        }

        private bool V2AccountFlat()
        {
            if (targetAccount == null) return false;
            lock (targetAccount.Positions)
                if (targetAccount.Positions.Any(p => p.MarketPosition != MarketPosition.Flat && p.Quantity != 0)) return false;
            lock (targetAccount.Orders)
                if (targetAccount.Orders.Any(o => !IsProtectionOrderTerminal(o.OrderState))) return false;
            lock (ticketMappings) if (ticketMappings.Count != 0) return false;
            return V2TransitionsDone();
        }
        private bool V2TransitionsDone()
        {
            lock (pendingEntries) if (pendingEntries.Count != 0) return false;
            lock (fullCloseLock) if (pendingFullCloseRequests.Count != 0) return false;
            lock (orphanCloseLock) if (pendingOrphanCloseRequests.Count != 0) return false;
            lock (protectionReplacementLock) if (pendingProtectionReplacements.Count != 0) return false;
            lock (trackedOrdersLock)
            {
                foreach (var pair in trackedOrders)
                {
                    Order order = pair.Key; TrackedOrderInfo info = pair.Value;
                    if (order == null || info == null) continue;
                    if (IsProtectionOrderTerminal(order.OrderState))
                    {
                        if (!info.TerminalHandled) return false;
                        continue;
                    }
                    bool protection = info.Role == TrackedOrderRole.StopLoss || info.Role == TrackedOrderRole.TakeProfit;
                    if (!protection || order.OrderState != OrderState.Working) return false;
                }
            }
            return true; // 정상 Working SL/TP는 SYNC를 막지 않는다.
        }
// R11 StartMMFListener moved to isolated orchestration
// R11 V2StopReadLoop moved to isolated orchestration
// R11 StopMMFListener moved to isolated orchestration
// R11 ListenToMMF moved to isolated orchestration
// R11 V2Pump moved to isolated orchestration
        private void ProcessRawMessage(OzMasterReader.Envelope item)
        {
            OzMasterReader.Command c = item.Trade;
            PrintLog($"[MMF v2 EXECUTOR_DISPATCH] Stream={v2StreamId} | Session={item.Session} | Sequence={item.Sequence} | Account={targetAccount.Name} | MasterTicket={c.Ticket} | RAW={item.Payload}");
            R6Before(item);
            var selection=EnableSnapshot;
            if((c.Action=="BUY"||c.Action=="SELL")&&(!selection.Enabled||item.PublishedTick<selection.Since))
                PrintLog("[ENTRY_BLOCKED] DISABLED_AT_SIGNAL | Ticket="+c.Ticket);
            else RouteSignal(c.Action, c.Symbol, c.SlDist, c.Price, c.Sl, c.Tp, c.Ticket, c.TargetVolume, c.ClosedVolume, "");
            R6After(item);
        }

        private Account targetAccount;

        // 진입 대기정보는 Master Ticket으로 관리하고, 실제 Ninja 주문은 Order 객체로 별도 추적한다.
        private class PendingEntryInfo
        {
            public ulong MasterTicket;
            public double SlDist;
            public double MasterEntryPrice;
            public double MasterTpPrice;
            public string NinjaSymbol;
            public string Direction;
            public int Contracts;
            public Order EntryOrder;
            // 미체결 진입을 취소하는 동안 추가 체결되어도 전체청산 의도를 유지한다.
            public bool CloseAllRequested;
        }

        private Dictionary<ulong, PendingEntryInfo> pendingEntries = new Dictionary<ulong, PendingEntryInfo>();

        // 청산 진행 여부는 Master Ticket으로 관리한다.
        private HashSet<ulong> pendingCloseMasterTickets = new HashSet<ulong>();

        // [Close Safety] 부분/전체청산을 목표 잔여수량으로 관리하고, 보호주문 종료 확인 뒤 시장가 청산을 제출한다.
        // 청산이 Rejected/Cancelled되거나 부분체결 후 취소되면 실제 잔여 계약수 기준으로 보호주문을 복구한다.
        private class PendingFullCloseRequest
        {
            public ulong MasterTicket;
            public string NinjaSymbol;
            public Order CloseOrder;
            public bool CloseSubmitted;
            public long RequestedTicks;
            public long LastProtectionFillTicks;
            public bool CloseAll;
            public int TargetRemainingContracts = -1;
            public List<double> CloseRatios = new List<double>();
            public int Revision;
            public int SubmittedRevision;
            public double SourceRemainingRatio = -1;
            public int FailureCount;
            public long RetryNotBeforeTicks;
            public bool RecoveringProtection;
        }

        private Dictionary<ulong, PendingFullCloseRequest> pendingFullCloseRequests = new Dictionary<ulong, PendingFullCloseRequest>();
        private readonly object fullCloseLock = new object();
        private long lastFullCloseCheckTicks = 0;

        // [고아 포지션 / Symbol 직렬화] 마스터에 없는 stale mapping은 Ticket별로 각각 flatten하지 않는다.
        // Ninja Position은 계좌+Instrument의 net position이므로, 같은 NinjaSymbol의 mapping이 전부 orphan일 때만
        // 해당 심볼의 stale Ticket 전체 SL/TP 종료 확인 -> 실제 Position 1회 조회 -> 심볼당 시장가 1개 -> Flat 확인 후 일괄 제거한다.
        private class PendingOrphanCloseRequest
        {
            public string NinjaSymbol;
            public List<ulong> StaleMasterTickets = new List<ulong>();
            public ulong TrackingMasterTicket;
            public Order CloseOrder;
            public bool CloseSubmitted;
            public bool CloseFilledAwaitingFlat;
            public bool BlockedByMasterLive;
            public long AccountedOrphanFilled;
            public long LastProtectionFillTicks;
            public long LastCloseFillTicks;
            public long RetryNotBeforeTicks;
        }

        // 고아청산 상태의 유일 키는 Master Ticket이 아니라 NinjaSymbol이다.
        private Dictionary<string, PendingOrphanCloseRequest> pendingOrphanCloseRequests =
            new Dictionary<string, PendingOrphanCloseRequest>(StringComparer.OrdinalIgnoreCase);
        private readonly object orphanCloseLock = new object();

        // [7단계 수정1] 부분청산의 SL/TP 교체와 SYNC 복구가 동시에 보호주문을 만들지 못하도록 직렬화한다.
        private readonly object protectionReplacementLock = new object();
        private readonly long protectionReplacementGuardTicks = TimeSpan.FromMilliseconds(1500).Ticks;

        // [7단계] 마스터 SYNC heartbeat 수신 시각과 통신 Watchdog 상태
        private long lastSyncReceivedTicks = 0;
        private long lastSyncWatchdogCheckTicks = 0;
        private volatile bool syncDisconnectWarningShown = false;
        private bool firstSyncLogged = false;

        private class MasterSyncState
        {
            public double SlPrice;
            public double TpPrice;
        }

        private class CopyPositionMap
        {
            public ulong MasterTicket;
            public string NinjaSymbol;
            public Order EntryOrder;
            public Order SlOrder;
            public Order TpOrder;
            public string Direction;
            public int InitialContracts;
            public int CurrentContracts;
            public double MasterEntryPrice;
            public double AverageFillPrice;
            public double CurrentSlPrice;
            public double CurrentTpPrice;
            public string ProtectionOcoId;
            public bool ProtectionReplaceInProgress;
            public long ProtectionReplaceGuardUntilTicks;
            public bool QuantityFault;
        }

        private Dictionary<ulong, CopyPositionMap> ticketMappings = new Dictionary<ulong, CopyPositionMap>();

        // [Order Object Tracking] 외부 주문명에 내부 식별자를 싣지 않고 Order 객체 자체로 Master Ticket/역할을 추적한다.
        private enum TrackedOrderRole
        {
            Entry,
            StopLoss,
            TakeProfit,
            PartialClose,
            FullClose,
            OrphanClose
        }

        private class TrackedOrderInfo
        {
            public ulong MasterTicket;
            public TrackedOrderRole Role;
            public long ExpireAtTicks;
            public bool TerminalSeen;
            // 티켓 잔여수량에 이미 반영한 누적 청산 체결량. OrderUpdate/ExecutionUpdate 중복 차감을 막는다.
            public int AppliedExitFilled;
            public int AppliedEntryFilled;
            public int ObservedFilled;
            public double ObservedAverageFillPrice;
            public int LifecycleFilled;
            public bool TerminalHandled;
            public bool TerminalObserved;
            public OrderState TerminalState;
            public PendingEntryInfo EntryContext;
        }

        // Order 객체의 참조 자체를 키로 사용한다. OrderId는 주문 생명주기 중 변경될 수 있으므로 추적키로 사용하지 않는다.
        private sealed class OrderReferenceComparer : IEqualityComparer<Order>
        {
            public bool Equals(Order x, Order y)
            {
                return object.ReferenceEquals(x, y);
            }

            public int GetHashCode(Order obj)
            {
                return System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(obj);
            }
        }

        private readonly object trackedOrdersLock = new object();
        private Dictionary<Order, TrackedOrderInfo> trackedOrders =
            new Dictionary<Order, TrackedOrderInfo>(new OrderReferenceComparer());

        // [Order Object Tracking TTL] terminal 주문은 즉시 삭제하지 않고 2분 유예 후 백그라운드에서 수거한다.
        // 포지션 종료 시 해당 Ticket의 잔존 추적 객체에도 TTL을 부여하되, terminal 이벤트가 확인된 객체만 실제 삭제한다.
        private readonly long trackedOrderTtlTicks = TimeSpan.FromMinutes(2).Ticks;
        private readonly long trackedOrderCleanupIntervalTicks = TimeSpan.FromSeconds(5).Ticks;
        private long lastTrackedOrderCleanupTicks = 0;

        // [OCO State Machine] 기존 보호주문의 실제 취소 완료를 확인한 뒤 다음 OCO 세대를 제출한다.
        private class PendingProtectionReplacement
        {
            public ulong MasterTicket;
            public string NinjaSymbol;
            public double SlPrice;
            public double TpPrice;
            public int Quantity;
            public string Reason;
            public long RequestedTicks;
        }

        private Dictionary<ulong, PendingProtectionReplacement> pendingProtectionReplacements = new Dictionary<ulong, PendingProtectionReplacement>();
        private long lastProtectionReplacementCheckTicks = 0;

// R11 BuildUI moved to isolated orchestration

// R11 OnWindowLoaded moved to isolated orchestration

// R11 OnWindowClosing moved to isolated orchestration

        // 드롭다운에서 계좌를 변경할 때마다 자동으로 이벤트 훅을 교체
// R11 CmbAccounts_SelectionChanged moved to isolated orchestration

// R11 PrintLog moved to isolated orchestration









        // [2단계] 동일 Master Ticket의 중복 진입 방지
        private bool IsMasterTicketAlreadyActive(ulong masterTicket)
        {
            if (masterTicket == 0) return false;

            // 이미 체결되어 확정 매핑된 티켓인지 확인
            lock (ticketMappings)
            {
                if (ticketMappings.ContainsKey(masterTicket))
                    return true;
            }

            // 아직 Ninja 브로커 체결 대기 중인 티켓인지 확인
            lock (pendingEntries)
            {
                if (pendingEntries.ContainsKey(masterTicket))
                    return true;
            }

            return false;
        }

        private void RegisterTrackedOrder(Order order, ulong masterTicket, TrackedOrderRole role)
        {
            if (order == null || masterTicket == 0) return;

            lock (trackedOrdersLock)
            {
                trackedOrders[order] = new TrackedOrderInfo
                {
                    MasterTicket = masterTicket,
                    Role = role,
                    ExpireAtTicks = 0,
                    TerminalSeen = false
                };
            }
        }

        private bool TryGetTrackedOrderInfo(Order order, out TrackedOrderInfo info)
        {
            info = null;
            if (order == null) return false;

            lock (trackedOrdersLock)
            {
                return trackedOrders.TryGetValue(order, out info);
            }
        }

        private List<Order> GetTrackedOrdersForTicket(ulong masterTicket, params TrackedOrderRole[] roles)
        {
            if (masterTicket == 0) return new List<Order>();

            lock (trackedOrdersLock)
            {
                return trackedOrders
                    .Where(kv => kv.Key != null &&
                                 kv.Value != null &&
                                 kv.Value.MasterTicket == masterTicket &&
                                 (roles == null || roles.Length == 0 || roles.Contains(kv.Value.Role)))
                    .Select(kv => kv.Key)
                    .ToList();
            }
        }

        // Filled/Cancelled/Rejected가 실제 관측된 주문은 마지막 terminal 이벤트 시점부터 2분 뒤 수거 대상으로 만든다.
        private void ScheduleTrackedOrderCleanup(Order order)
        {
            if (order == null) return;

            long expireAtTicks = DateTime.UtcNow.Ticks + trackedOrderTtlTicks;

            lock (trackedOrdersLock)
            {
                TrackedOrderInfo info;
                if (!trackedOrders.TryGetValue(order, out info) || info == null)
                    return;

                info.TerminalSeen = true;
                if (info.ExpireAtTicks < expireAtTicks)
                    info.ExpireAtTicks = expireAtTicks;
            }
        }

        // 포지션/매핑의 생명주기가 끝난 Ticket은 잔존 Order 객체 전체에 동일한 2분 TTL을 부여한다.
        // 아직 terminal 상태를 관측하지 못한 객체는 TTL이 지나도 삭제하지 않고 terminal 이벤트를 기다린다.
        private void ScheduleTrackedOrdersForTicketCleanup(ulong masterTicket)
        {
            if (masterTicket == 0) return;

            long expireAtTicks = DateTime.UtcNow.Ticks + trackedOrderTtlTicks;

            lock (trackedOrdersLock)
            {
                foreach (KeyValuePair<Order, TrackedOrderInfo> kv in trackedOrders)
                {
                    TrackedOrderInfo info = kv.Value;
                    if (info == null || info.MasterTicket != masterTicket)
                        continue;

                    if (info.ExpireAtTicks < expireAtTicks)
                        info.ExpireAtTicks = expireAtTicks;
                }
            }
        }

        // MMF 리스너의 저빈도 백그라운드 청소에서 terminal 확인 + TTL 만료 객체만 조용히 제거한다.
        private void CleanupExpiredTrackedOrders()
        {
            long nowTicks = DateTime.UtcNow.Ticks;

            lock (trackedOrdersLock)
            {
                List<Order> expiredOrders = trackedOrders
                    .Where(kv => kv.Key != null &&
                                 kv.Value != null &&
                                 kv.Value.TerminalSeen &&
                                 kv.Value.ExpireAtTicks > 0 &&
                                 kv.Value.ExpireAtTicks <= nowTicks)
                    .Select(kv => kv.Key)
                    .ToList();

                foreach (Order order in expiredOrders)
                    trackedOrders.Remove(order);
            }
        }

        private void UnregisterTrackedOrder(Order order)
        {
            if (order == null) return;

            lock (trackedOrdersLock)
            {
                trackedOrders.Remove(order);
            }
        }

        private void ClearTrackedOrderState(string reason)
        {
            int count;
            lock (trackedOrdersLock)
            {
                count = trackedOrders.Count;
                trackedOrders.Clear();
            }

            if (count > 0)
                PrintLog($"🧹 [주문 객체 추적 초기화] {count}건 | {reason}");
        }

        // protectionReplacementLock 안에서 호출한다.
        private bool IsProtectionReplacementGuardActive(CopyPositionMap map)
        {
            if (map == null) return false;
            return map.ProtectionReplaceInProgress || DateTime.UtcNow.Ticks < map.ProtectionReplaceGuardUntilTicks;
        }

        // [OCO] 보호 SL/TP 한 세트마다 절대로 재사용하지 않는 고유 OCO ID를 발급한다.
        private string CreateProtectionOcoId()
        {
            return Guid.NewGuid().ToString("N");
        }

        // [OCO] 현재 목표 SL/TP를 한 세트로 제출한다.
        // TP가 있으면 SL/TP를 동일한 신규 OCO ID로 묶어 동시에 Submit하고, TP가 없으면 SL만 독립 주문으로 제출한다.
        private bool SubmitProtectionSet(CopyPositionMap map, Instrument inst, int quantity, double slPrice, double tpPrice, string reason)
        {
            if (targetAccount == null || map == null || inst == null || quantity <= 0 || slPrice <= 0.0)
                return false;

            OrderAction exitAction = (map.Direction == "BUY") ? OrderAction.Sell : OrderAction.BuyToCover;
            string ocoId = (tpPrice > 0.0) ? CreateProtectionOcoId() : "";
            Order tpOrder = null;
            bool submissionAttempted = false;
            Order slOrder = null;

            try
            {
                // OCO 쌍을 만들 때는 TP 객체부터 준비한다. TP 생성이 실패하면 SL은 OCO 없이 단독 제출해 보호를 우선한다.
                if (tpPrice > 0.0)
                {
                    tpOrder = targetAccount.CreateOrder(
                        inst,
                        exitAction,
                        OrderType.Limit,
                        OrderEntry.Automated,
                        TimeInForce.Gtc,
                        quantity,
                        tpPrice,
                        0,
                        ocoId,
                        "",
                        Core.Globals.MaxDate,
                        null);

                    if (tpOrder == null)
                    {
                        PrintLog($"❌ [OCO TP 생성 실패] MT5 Ticket: {map.MasterTicket} | {reason} | SL 단독 보호로 전환합니다.");
                        ocoId = "";
                    }
                }

                slOrder = targetAccount.CreateOrder(
                    inst,
                    exitAction,
                    OrderType.StopMarket,
                    OrderEntry.Automated,
                    TimeInForce.Gtc,
                    quantity,
                    0,
                    slPrice,
                    ocoId,
                    "",
                    Core.Globals.MaxDate,
                    null);

                if (slOrder == null)
                {
                    PrintLog($"❌ [OCO SL 생성 실패] MT5 Ticket: {map.MasterTicket} | {reason} | 보호주문 제출을 중단합니다.");
                    return false;
                }

                map.CurrentSlPrice = slPrice;
                map.CurrentTpPrice = tpPrice;
                map.SlOrder = slOrder;
                RegisterTrackedOrder(slOrder, map.MasterTicket, TrackedOrderRole.StopLoss);

                if (tpOrder != null && !string.IsNullOrEmpty(ocoId))
                {
                    map.TpOrder = tpOrder;
                    map.ProtectionOcoId = ocoId;
                    RegisterTrackedOrder(tpOrder, map.MasterTicket, TrackedOrderRole.TakeProfit);

                    // Submit 안에서 OrderUpdate가 즉시 발생해도 이미 객체 추적 등록이 끝난 상태다.
                    submissionAttempted = true;
                    targetAccount.Submit(new[] { slOrder, tpOrder });
                    PrintLog($"🛡️ [OCO 보호주문 제출] MT5 Ticket: {map.MasterTicket} | {quantity}계약 | SL: {slPrice} | TP: {tpPrice} | OCO: {ocoId} | {reason}");
                }
                else
                {
                    map.TpOrder = null;
                    map.ProtectionOcoId = "";

                    // Submit 안에서 OrderUpdate가 즉시 발생해도 이미 객체 추적 등록이 끝난 상태다.
                    submissionAttempted = true;
                    targetAccount.Submit(new[] { slOrder });
                    PrintLog($"🛡️ [단독 SL 제출] MT5 Ticket: {map.MasterTicket} | {quantity}계약 | SL: {slPrice} | {reason}");
                }

                return true;
            }
            catch (Exception ex)
            {
                // Submit 예외 뒤에도 실제 접수/체결될 수 있으므로 제출한 객체는 계속 추적한다.
                if (!submissionAttempted)
                {
                    UnregisterTrackedOrder(slOrder);
                    UnregisterTrackedOrder(tpOrder);
                    if (object.ReferenceEquals(map.SlOrder, slOrder)) map.SlOrder = null;
                    if (object.ReferenceEquals(map.TpOrder, tpOrder)) map.TpOrder = null;
                    map.ProtectionOcoId = "";
                }

                PrintLog($"⚠️ [OCO 보호주문 오류] MT5 Ticket: {map.MasterTicket} | {reason} | {ex.Message}");
                return false;
            }
        }

        // [OCO State Machine] 보호주문이 완전히 종료된 상태인지 판정한다.
        private bool IsProtectionOrderTerminal(OrderState state)
        {
            return state == OrderState.Cancelled ||
                   state == OrderState.Rejected ||
                   state == OrderState.Filled;
        }

        private bool IsProtectionOrderForTicket(Order order, ulong masterTicket)
        {
            if (order == null || masterTicket == 0) return false;

            TrackedOrderInfo info;
            return TryGetTrackedOrderInfo(order, out info) &&
                   info != null &&
                   info.MasterTicket == masterTicket &&
                   (info.Role == TrackedOrderRole.StopLoss || info.Role == TrackedOrderRole.TakeProfit);
        }

        private List<Order> GetOutstandingProtectionOrders(ulong masterTicket)
        {
            if (masterTicket == 0) return new List<Order>();

            return GetTrackedOrdersForTicket(
                    masterTicket,
                    TrackedOrderRole.StopLoss,
                    TrackedOrderRole.TakeProfit)
                .Where(o => o != null && !IsTrackedOrderTerminal(o))
                .ToList();
        }

        // 이미 취소 진행 중인 주문에는 Cancel을 중복 전송하지 않는다.
        // ChangePending/ChangeSubmitted 주문은 상태가 다시 취소 가능한 상태가 된 뒤 OrderUpdate에서 재시도한다.
        private bool CanRequestProtectionCancel(Order order)
        {
            if (order == null) return false;

            return order.OrderState == OrderState.Submitted ||
                   order.OrderState == OrderState.Accepted ||
                   order.OrderState == OrderState.Working ||
                   order.OrderState == OrderState.PartFilled ||
                   order.OrderState == OrderState.TriggerPending;
        }

        private void RequestOutstandingProtectionCancels(ulong masterTicket, string reason)
        {
            if (targetAccount == null || masterTicket == 0) return;

            List<Order> outstanding = GetOutstandingProtectionOrders(masterTicket);
            if (outstanding.Count == 0) return;

            // 같은 OCO 그룹은 대표 주문 하나만 취소해도 짝 주문이 같이 취소된다.
            List<Order> cancelRequests = new List<Order>();
            HashSet<string> seenOcoIds = new HashSet<string>();

            foreach (Order order in outstanding)
            {
                if (!CanRequestProtectionCancel(order)) continue;

                if (!string.IsNullOrEmpty(order.Oco))
                {
                    if (seenOcoIds.Add(order.Oco)) cancelRequests.Add(order);
                }
                else
                {
                    cancelRequests.Add(order);
                }
            }

            if (cancelRequests.Count == 0) return;

            try
            {
                targetAccount.Cancel(cancelRequests.ToArray());
                PrintLog($"⏳ [보호주문 취소요청] MT5 Ticket: {masterTicket} | {cancelRequests.Count}개 Cancel 요청 | 실제 취소 완료 대기 | {reason}");
            }
            catch (Exception ex)
            {
                PrintLog($"⚠️ [보호주문 취소요청 오류] MT5 Ticket: {masterTicket} | {reason} | {ex.Message}");
            }
        }

        private void ClearPendingProtectionReplacementState(string reason)
        {
            List<CopyPositionMap> maps;
            lock (ticketMappings)
            {
                maps = ticketMappings.Values.Where(m => m != null).ToList();
            }

            int clearedCount;
            lock (protectionReplacementLock)
            {
                clearedCount = pendingProtectionReplacements.Count;
                pendingProtectionReplacements.Clear();

                foreach (CopyPositionMap map in maps)
                {
                    map.ProtectionReplaceInProgress = false;
                    map.ProtectionReplaceGuardUntilTicks = 0;
                }
            }

            if (clearedCount > 0)
                PrintLog($"🧹 [OCO 교체 상태 초기화] {clearedCount}건 | {reason}");
        }

        private void AbortPendingProtectionReplacement(ulong masterTicket, string reason)
        {
            lock (protectionReplacementLock)
            {
                if (!pendingProtectionReplacements.Remove(masterTicket)) return;
            }

            CopyPositionMap map = null;
            lock (ticketMappings)
            {
                ticketMappings.TryGetValue(masterTicket, out map);
            }

            if (map != null)
            {
                lock (protectionReplacementLock)
                {
                    map.ProtectionReplaceInProgress = false;
                    map.ProtectionReplaceGuardUntilTicks = DateTime.UtcNow.Ticks + protectionReplacementGuardTicks;
                }
            }

            PrintLog($"🛑 [OCO 교체 중단] MT5 Ticket: {masterTicket} | {reason}");
        }

        // [Close Safety] 계좌 변경/종료 시 이전 계좌의 청산 상태가 새 계좌로 이어지지 않게 내부 상태를 비운다.
        private void ClearPendingCloseState(string reason)
        {
            int fullCloseCount;
            int orphanCloseCount;
            int closeTicketCount;

            lock (fullCloseLock)
            {
                fullCloseCount = pendingFullCloseRequests.Count;
                pendingFullCloseRequests.Clear();
            }

            lock (orphanCloseLock)
            {
                orphanCloseCount = pendingOrphanCloseRequests.Count;
                pendingOrphanCloseRequests.Clear();
            }

            lock (pendingCloseMasterTickets)
            {
                closeTicketCount = pendingCloseMasterTickets.Count;
                pendingCloseMasterTickets.Clear();
            }

            if (fullCloseCount > 0 || orphanCloseCount > 0 || closeTicketCount > 0)
                PrintLog($"🧹 [청산 상태 초기화] 전체청산 {fullCloseCount}건 / 고아심볼청산 {orphanCloseCount}건 / pending {closeTicketCount}건 | {reason}");
        }

        private bool IsFullClosePending(ulong masterTicket)
        {
            lock (fullCloseLock)
            {
                return pendingFullCloseRequests.ContainsKey(masterTicket);
            }
        }

        private void RemoveFullClosePending(ulong masterTicket)
        {
            lock (fullCloseLock)
            {
                pendingFullCloseRequests.Remove(masterTicket);
            }

            lock (pendingCloseMasterTickets)
            {
                pendingCloseMasterTickets.Remove(masterTicket);
            }
        }

        private bool IsTicketExitRole(TrackedOrderRole role)
        {
            return role == TrackedOrderRole.StopLoss ||
                   role == TrackedOrderRole.TakeProfit ||
                   role == TrackedOrderRole.PartialClose ||
                   role == TrackedOrderRole.FullClose;
        }

        // 종목 전체 Position.Quantity는 어느 Master Ticket이 체결됐는지 알려주지 않는다.
        // 추적 중인 티켓별 청산 주문의 누적 Filled 증가분만 해당 티켓에서 한 번씩 차감한다.
        private void RefreshTicketExitQuantities()
        {
            // 진입 증가분을 먼저 반영하여 청산 이벤트가 먼저 처리돼도 티켓 수량을 보존한다.
            RefreshTicketEntryQuantities();
            lock (ticketMappings)
            {
                lock (trackedOrdersLock)
                {
                    foreach (KeyValuePair<Order, TrackedOrderInfo> item in trackedOrders)
                    {
                        Order order = item.Key;
                        TrackedOrderInfo tracking = item.Value;
                        if (order == null || tracking == null || !IsTicketExitRole(tracking.Role)) continue;

                        CopyPositionMap map;
                        if (!ticketMappings.TryGetValue(tracking.MasterTicket, out map) || map == null) continue;

                        int cumulativeFilled = Math.Max(tracking.ObservedFilled, Math.Max(0, order.Filled));
                        int newlyFilled = cumulativeFilled - tracking.AppliedExitFilled;
                        if (newlyFilled <= 0) continue;

                        if (newlyFilled > map.CurrentContracts)
                        {
                            map.QuantityFault = true;
                            PrintLog($"🚨 [티켓 초과체결 감지] MT5 Ticket: {map.MasterTicket} | 잔여 {map.CurrentContracts} / 추가 청산체결 {newlyFilled} | 수량 확인 전 추가 발주 차단");
                        }
                        if(tracking.Role==TrackedOrderRole.StopLoss||tracking.Role==TrackedOrderRole.TakeProfit) R6Record(map.MasterTicket).LocalExit=true;
                        map.CurrentContracts = Math.Max(0, map.CurrentContracts - newlyFilled);
                        tracking.AppliedExitFilled = cumulativeFilled;
                    }
                }
            }
        }


        // 수량 누계와 체결 평균가는 OrderUpdate/ExecutionUpdate가 공유한다.
        // 원본 Order 객체가 이미 다음 상태로 바뀌어도 누계가 뒤로 가지 않는다.
        private void RefreshTicketEntryQuantities()
        {
            List<KeyValuePair<Order, TrackedOrderInfo>> entries;
            lock (trackedOrdersLock)
            {
                entries = trackedOrders.Where(kv => kv.Value != null &&
                    kv.Value.Role == TrackedOrderRole.Entry && kv.Value.EntryContext != null).ToList();
            }

            foreach (KeyValuePair<Order, TrackedOrderInfo> item in entries)
            {
                Order order = item.Key;
                TrackedOrderInfo tracking = item.Value;
                PendingEntryInfo context = tracking.EntryContext;
                int filled = Math.Max(tracking.ObservedFilled, Math.Max(0, order.Filled));
                double average = tracking.ObservedAverageFillPrice;
                if (order.Filled >= tracking.ObservedFilled && order.AverageFillPrice > 0.0)
                    average = order.AverageFillPrice;
                int delta = filled - tracking.AppliedEntryFilled;
                CopyPositionMap map;
                lock (ticketMappings)
                {
                    ticketMappings.TryGetValue(context.MasterTicket, out map);
                    if (map == null && delta > 0)
                    {
                        map = new CopyPositionMap
                        {
                            MasterTicket = context.MasterTicket,
                            NinjaSymbol = context.NinjaSymbol,
                            Direction = context.Direction,
                            EntryOrder = order,
                            MasterEntryPrice = context.MasterEntryPrice,
                            ProtectionOcoId = ""
                        };
                        ticketMappings[context.MasterTicket] = map;
                    }
                    if (map == null) continue;
                    if (delta > 0)
                    {
                        map.InitialContracts += delta;
                        map.CurrentContracts += delta;
                        tracking.AppliedEntryFilled = filled;
                    }
                    // 이미 MODIFY/SYNC로 바뀐 가격은 평균가 대비 오프셋을 유지한다.
                    if (average > 0.0 && average != map.AverageFillPrice)
                    {
                        double slOffset = map.CurrentSlPrice > 0.0 && map.AverageFillPrice > 0.0
                            ? map.CurrentSlPrice - map.AverageFillPrice
                            : (context.Direction == "BUY" ? -context.SlDist : context.SlDist);
                        double tpOffset = map.AverageFillPrice > 0.0
                            ? (map.CurrentTpPrice > 0.0 ? map.CurrentTpPrice - map.AverageFillPrice : 0.0)
                            : (context.MasterTpPrice > 0.0 && context.MasterEntryPrice > 0.0
                                ? context.MasterTpPrice - context.MasterEntryPrice : 0.0);
                        map.AverageFillPrice = average;
                        map.CurrentSlPrice = order.Instrument.MasterInstrument.RoundToTickSize(average + slOffset);
                        map.CurrentTpPrice = tpOffset != 0.0
                            ? Math.Max(0.0, order.Instrument.MasterInstrument.RoundToTickSize(average + tpOffset)) : 0.0;
                    }
                }
            }
        }

        private bool IsTrackedOrderTerminal(Order order)
        {
            if (order == null) return true;
            TrackedOrderInfo tracking;
            TryGetTrackedOrderInfo(order, out tracking);
            OrderState state = tracking != null && tracking.TerminalObserved ? tracking.TerminalState : order.OrderState;
            if (order.OrderState == OrderState.Filled) state = OrderState.Filled;
            if (state == OrderState.Filled)
                return Math.Max(order.Filled, tracking != null ? tracking.ObservedFilled : 0) >= order.Quantity;
            return state == OrderState.Cancelled || state == OrderState.Rejected;
        }

        private bool HasOutstandingEntry(ulong masterTicket)
        {
            return GetTrackedOrdersForTicket(masterTicket, TrackedOrderRole.Entry)
                .Any(o => !IsTrackedOrderTerminal(o));
        }

        private void RequestEntryCancel(ulong masterTicket)
        {
            List<Order> entries = GetTrackedOrdersForTicket(masterTicket, TrackedOrderRole.Entry)
                .Where(o => !IsTrackedOrderTerminal(o) && CanRequestProtectionCancel(o)).ToList();
            if (entries.Count == 0) return;
            try { targetAccount.Cancel(entries.ToArray()); }
            catch (Exception ex) { PrintLog($"⚠️ [진입 잔량 취소 대기] MT5 Ticket: {masterTicket} | {ex.Message}"); }
        }

        // 부분체결 때마다 한 세대의 SL/TP만 유지하고, 수량/평균가가 달라지면 취소 완료 후 교체한다.
        private void EnsureTicketProtection(CopyPositionMap map, string reason)
        {
            if (map == null || map.QuantityFault || map.CurrentContracts <= 0 || map.CurrentSlPrice <= 0.0) return;
            if (IsOrphanClosePending(map.MasterTicket)) return;
            if (IsFullClosePending(map.MasterTicket) && !HasOutstandingEntry(map.MasterTicket) &&
                !R8RecoveringProtection(map.MasterTicket)) return;
            bool replacementPending;
            lock (protectionReplacementLock)
                replacementPending = pendingProtectionReplacements.ContainsKey(map.MasterTicket);
            List<Order> outstanding = GetOutstandingProtectionOrders(map.MasterTicket);
            bool slMatches = map.SlOrder != null && !IsTrackedOrderTerminal(map.SlOrder) &&
                Math.Max(0, map.SlOrder.Quantity - map.SlOrder.Filled) == map.CurrentContracts &&
                Math.Abs(map.SlOrder.StopPrice - map.CurrentSlPrice) < 0.0000001;
            bool tpMatches = map.CurrentTpPrice <= 0.0
                ? map.TpOrder == null || IsTrackedOrderTerminal(map.TpOrder)
                : map.TpOrder != null && !IsTrackedOrderTerminal(map.TpOrder) &&
                    Math.Max(0, map.TpOrder.Quantity - map.TpOrder.Filled) == map.CurrentContracts &&
                    Math.Abs(map.TpOrder.LimitPrice - map.CurrentTpPrice) < 0.0000001;
            int expectedCount = map.CurrentTpPrice > 0.0 ? 2 : 1;
            if (!replacementPending && slMatches && tpMatches && outstanding.Count == expectedCount) return;
            Instrument inst = Instrument.GetInstrument(map.NinjaSymbol);
            if (inst == null) return;
            ReplaceProtectionSet(map, inst, map.CurrentSlPrice, map.CurrentTpPrice, reason);
        }

        // 수량 0만으로 지우지 않는다. 늦은 진입/보호 체결을 받을 주문 추적이 끝나야 종료한다.
        private void TryFinalizeTicket(ulong masterTicket)
        {
            RefreshTicketExitQuantities();
            CopyPositionMap map;
            lock (ticketMappings) ticketMappings.TryGetValue(masterTicket, out map);
            if (map == null || map.CurrentContracts > 0 || map.QuantityFault || IsOrphanClosePending(masterTicket)) return;
            if (HasOutstandingEntry(masterTicket))
            {
                RequestEntryCancel(masterTicket);
                return;
            }
            if (GetOutstandingProtectionOrders(masterTicket).Count > 0)
            {
                RequestOutstandingProtectionCancels(masterTicket, "티켓 잔여 0 / 잔존 보호주문 종료");
                return;
            }
            if (GetTrackedOrdersForTicket(masterTicket, TrackedOrderRole.PartialClose, TrackedOrderRole.FullClose)
                .Any(o => !IsTrackedOrderTerminal(o))) return;
            if (!risk.CanFinalize(masterTicket, map.InitialContracts) || !risk.Finalize(masterTicket, map.InitialContracts)) return;
            AbortPendingProtectionReplacement(masterTicket, "티켓 잔여 0 및 관련 주문 종료 확인");
            RemoveFullClosePending(masterTicket);
            lock (pendingEntries) pendingEntries.Remove(masterTicket);
            lock (ticketMappings) ticketMappings.Remove(masterTicket);
            ScheduleTrackedOrdersForTicketCleanup(masterTicket);
            PrintLog($"✅ [티켓 종료] MT5 Ticket: {masterTicket} | 티켓 잔여 0 / 진입·청산·보호주문 종료 확인");
        }

        private void ResolveCloseTarget(PendingFullCloseRequest request, int currentContracts, int initialContracts)
        {
            if (request.CloseAll)
            {
                request.TargetRemainingContracts = 0;
                request.CloseRatios.Clear();
                return;
            }
            if (request.SourceRemainingRatio >= 0.0)
            {
                // 최초 실제 체결량 대비 누적 잔량. 1계약 미만 감소를 전량청산으로 바꾸지 않는다.
                request.TargetRemainingContracts = request.SourceRemainingRatio == 0.0 ? 0 :
                    Math.Max(1, (int)Math.Ceiling(initialContracts * request.SourceRemainingRatio - 1e-9));
                request.CloseRatios.Clear();
                return;
            }
            if (request.TargetRemainingContracts < 0) request.TargetRemainingContracts = currentContracts;
            foreach (double ratio in request.CloseRatios)
            {
                int closeQty = (int)Math.Floor(request.TargetRemainingContracts * ratio + 1e-9);
                request.TargetRemainingContracts = Math.Max(0, request.TargetRemainingContracts - closeQty);
            }
            request.CloseRatios.Clear();
        }

        private void QueueTicketClose(ulong masterTicket, string ninjaSymbol, bool closeAll, double closeRatio, double remainingRatio = -1)
        {
            PendingFullCloseRequest request;
            lock (fullCloseLock)
            {
                if (!pendingFullCloseRequests.TryGetValue(masterTicket, out request))
                {
                    request = new PendingFullCloseRequest
                    {
                        MasterTicket = masterTicket, NinjaSymbol = ninjaSymbol,
                        RequestedTicks = DateTime.UtcNow.Ticks
                    };
                    pendingFullCloseRequests[masterTicket] = request;
                }
                if (closeAll)
                {
                    request.CloseAll = true;
                    request.TargetRemainingContracts = 0;
                    request.CloseRatios.Clear();
                }
                else if (!request.CloseAll)
                {
                    if (remainingRatio >= 0.0)
                        request.SourceRemainingRatio = request.SourceRemainingRatio < 0.0 ? remainingRatio :
                            Math.Min(request.SourceRemainingRatio, remainingRatio);
                    else request.CloseRatios.Add(closeRatio);
                }
                request.Revision++;
            }
            if (closeAll)
            {
                foreach (Order entry in GetTrackedOrdersForTicket(masterTicket, TrackedOrderRole.Entry))
                {
                    TrackedOrderInfo info;
                    if (TryGetTrackedOrderInfo(entry, out info) && info.EntryContext != null)
                        info.EntryContext.CloseAllRequested = true;
                }
            }
            lock (pendingCloseMasterTickets) pendingCloseMasterTickets.Add(masterTicket);
            TryAdvanceFullClose(masterTicket);
        }

        // 실제 net 수량은 티켓별 수량의 검증에만 사용한다. 다른 티켓의 물량을 이 티켓에 배분하지 않는다.
        private bool TryGetTicketScopedQuantity(CopyPositionMap map, Position pos, string reason, out int quantity)
        {
            quantity = 0;
            if (map == null || pos == null) return false;

            RefreshTicketExitQuantities();
            if (map.QuantityFault) return false;

            long mappedSymbolQuantity = 0;
            bool conflictingDirection = false;
            lock (ticketMappings)
            {
                CopyPositionMap current;
                if (!ticketMappings.TryGetValue(map.MasterTicket, out current) ||
                    !object.ReferenceEquals(current, map))
                    return false;

                quantity = Math.Max(0, map.CurrentContracts);
                // 이 티켓의 추적 청산 주문으로 이미 전량 체결된 경우 티켓 종료 정리를 허용한다.
                if (quantity == 0) return true;

                foreach (CopyPositionMap candidate in ticketMappings.Values)
                {
                    if (candidate == null || candidate.CurrentContracts <= 0 ||
                        !string.Equals(candidate.NinjaSymbol, map.NinjaSymbol, StringComparison.OrdinalIgnoreCase))
                        continue;

                    if (candidate.Direction != map.Direction)
                    {
                        conflictingDirection = true;
                        break;
                    }
                    mappedSymbolQuantity += candidate.CurrentContracts;
                }
            }

            MarketPosition expectedDirection = map.Direction == "BUY" ? MarketPosition.Long : MarketPosition.Short;
            if (conflictingDirection || mappedSymbolQuantity != pos.Quantity || pos.MarketPosition != expectedDirection)
            {
                PrintLog($"🚫 [티켓 수량 불일치] MT5 Ticket: {map.MasterTicket} | {map.NinjaSymbol} | 티켓 {quantity}계약 / 동일방향 매핑 합계 {mappedSymbolQuantity}계약 / 실제 {pos.Quantity}계약 / 반대방향 매핑={conflictingDirection} | 다른 티켓 물량을 사용하지 않고 보류 | {reason}");
                quantity = 0;
                return false;
            }

            return true;
        }

        // 청산 실패 복구는 해당 티켓의 체결 후 잔여수량을 사용한다.
        // 종목 전체 수량은 검증용이며 SL/TP 복구 수량으로 대입하지 않는다.
        private bool R8RecoveringProtection(ulong masterTicket)
        {
            lock (fullCloseLock)
            {
                PendingFullCloseRequest request;
                return pendingFullCloseRequests.TryGetValue(masterTicket, out request) &&
                    request.RecoveringProtection && !request.CloseSubmitted;
            }
        }

        private void RecoverProtectionAfterCloseFailure(ulong masterTicket, string reason, bool forceProtectionRebuild)
        {
            if (targetAccount == null || masterTicket == 0) return;
            PendingFullCloseRequest request;
            lock (fullCloseLock)
            {
                if (!pendingFullCloseRequests.TryGetValue(masterTicket, out request) || request.CloseSubmitted) return;
                request.FailureCount = Math.Min(10, request.FailureCount + 1);
                request.RetryNotBeforeTicks = DateTime.UtcNow.Ticks +
                    TimeSpan.FromSeconds(Math.Min(15, 1 << Math.Min(4, request.FailureCount - 1))).Ticks;
                request.RecoveringProtection = true;
            }
            RefreshTicketExitQuantities();
            CopyPositionMap map;
            lock (ticketMappings) ticketMappings.TryGetValue(masterTicket, out map);
            if (map == null) return;
            if (map.CurrentContracts == 0) TryFinalizeTicket(masterTicket);
            else EnsureTicketProtection(map, "청산 재시도 대기 중 잔량 보호 - " + reason);
            PrintLog($"⚠️ [청산 목표 유지] MT5 Ticket: {masterTicket} | 잔여 {map.CurrentContracts}계약 / 목표 {request.TargetRemainingContracts}계약 | {reason}");
        }

        // 전체청산 요청은 OCO 보호주문이 실제 terminal 상태가 된 후에만 시장가 주문을 제출한다.
        private void BeginFullClose(CopyPositionMap map, Instrument inst)
        {
            if (targetAccount == null || map == null || inst == null || map.MasterTicket == 0) return;
            QueueTicketClose(map.MasterTicket, map.NinjaSymbol, true, 1.0);
        }

        private void TryAdvanceFullClose(ulong masterTicket)
        {
            if (targetAccount == null || masterTicket == 0 || IsOrphanClosePending(masterTicket)) return;
            PendingFullCloseRequest request;
            lock (fullCloseLock)
                if (!pendingFullCloseRequests.TryGetValue(masterTicket, out request)) return;

            RefreshTicketExitQuantities();
            // CLOSE가 진입보다 먼저 처리돼도 버리지 않는다. 추가 진입 잔량 종료 후 체결량을 확정한다.
            if (HasOutstandingEntry(masterTicket))
            {
                RequestEntryCancel(masterTicket);
                return;
            }
            CopyPositionMap map;
            lock (ticketMappings) ticketMappings.TryGetValue(masterTicket, out map);
            if (map == null)
            {
                RemoveFullClosePending(masterTicket);
                lock (pendingEntries) pendingEntries.Remove(masterTicket);
                return;
            }
            if (map.QuantityFault) return;
            ResolveCloseTarget(request, map.CurrentContracts, map.InitialContracts);
            R6PlanClose(masterTicket,request.TargetRemainingContracts);
            // 후속 CLOSE는 목표 잔량만 갱신한다. 제출한 주문의 terminal 전에는 다음 주문을 만들지 않는다.
            if (request.CloseSubmitted) return;
            if (request.LastProtectionFillTicks > 0 &&
                DateTime.UtcNow.Ticks - request.LastProtectionFillTicks < TimeSpan.FromMilliseconds(500).Ticks) return;

            int required = Math.Max(0, map.CurrentContracts - request.TargetRemainingContracts);
            if (required == 0)
            {
                RemoveFullClosePending(masterTicket);
                if (map.CurrentContracts == 0) TryFinalizeTicket(masterTicket);
                else EnsureTicketProtection(map, "청산 목표 잔량 도달 후 보호 확인");
                return;
            }

            if (request.RetryNotBeforeTicks > DateTime.UtcNow.Ticks)
            {
                EnsureTicketProtection(map, "청산 목표 유지 / 재시도 간격");
                return;
            }
            request.RecoveringProtection = false;
            Instrument inst = Instrument.GetInstrument(map.NinjaSymbol);
            if (inst == null) return;
            Position pos = PositionSnapshot().FirstOrDefault(p => p.Instrument.FullName == inst.FullName);
            MarketPosition expected = map.Direction == "BUY" ? MarketPosition.Long : MarketPosition.Short;
            // Position 이벤트가 늦어도 잔여 매핑을 삭제하거나 다른 티켓의 수량을 가져오지 않는다.
            if (pos == null || pos.MarketPosition != expected || pos.Quantity <= 0) return;
            int ticketQty;
            if (!TryGetTicketScopedQuantity(map, pos, "티켓 청산 제출 직전", out ticketQty)) return;
            int closeQty = Math.Max(0, ticketQty - request.TargetRemainingContracts);
            if (closeQty == 0) return;

            AbortPendingProtectionReplacement(masterTicket, "티켓 청산이 보호주문 교체보다 우선합니다.");
            if (GetOutstandingProtectionOrders(masterTicket).Count > 0)
            {
                RequestOutstandingProtectionCancels(masterTicket, "부분/전체 청산 전 보호주문 종료");
                return;
            }

            Order closeOrder = null;
            try
            {
                OrderAction action = map.Direction == "BUY" ? OrderAction.Sell : OrderAction.BuyToCover;
                closeOrder = targetAccount.CreateOrder(inst, action, OrderType.Market, OrderEntry.Automated,
                    TimeInForce.Day, closeQty, 0, 0, "", "", Core.Globals.MaxDate, null);
                if (closeOrder == null)
                {
                    RecoverProtectionAfterCloseFailure(masterTicket, "시장가 청산 주문 생성 실패", true);
                    return;
                }
                RegisterTrackedOrder(closeOrder, masterTicket,
                    request.CloseAll ? TrackedOrderRole.FullClose : TrackedOrderRole.PartialClose);
                request.CloseOrder = closeOrder;
                request.CloseSubmitted = true;
                request.SubmittedRevision = request.Revision;
                targetAccount.Submit(new[] { closeOrder });
                PrintLog($"📤 [티켓 청산 제출] MT5 Ticket: {masterTicket} | {closeQty}계약 / 목표 잔여 {request.TargetRemainingContracts}계약 | 보호주문 종료 확인");
            }
            catch (Exception ex)
            {
                if (request.CloseSubmitted)
                {
                    // Submit 예외만으로 미접수를 단정하지 않는다. 기존 주문을 추적하고 중복 제출을 막는다.
                    PrintLog($"🚨 [청산 제출 결과 확인 대기] MT5 Ticket: {masterTicket} | 기존 주문 terminal 확인 전 재제출 보류 | {ex.Message}");
                }
                else RecoverProtectionAfterCloseFailure(masterTicket, "청산 주문 생성 오류: " + ex.Message, true);
            }
        }

        private void CheckPendingFullCloseRequests()
        {
            List<ulong> tickets;
            lock (fullCloseLock) tickets = pendingFullCloseRequests.Keys.ToList();
            foreach (ulong ticket in tickets)
            {
                PendingFullCloseRequest request;
                lock (fullCloseLock)
                    if (!pendingFullCloseRequests.TryGetValue(ticket, out request)) continue;
                if (request.CloseSubmitted && request.CloseOrder != null && IsTrackedOrderTerminal(request.CloseOrder))
                    ProcessOrderObservation(request.CloseOrder, request.CloseOrder.OrderState,
                        request.CloseOrder.Filled, request.CloseOrder.AverageFillPrice);
                else TryAdvanceFullClose(ticket);
            }
            List<ulong> zeroTickets;
            lock (ticketMappings)
                zeroTickets = ticketMappings.Values.Where(m => m.CurrentContracts == 0).Select(m => m.MasterTicket).ToList();
            foreach (ulong ticket in zeroTickets) TryFinalizeTicket(ticket);
        }

        private bool TryGetPendingOrphanSymbolForTicket(ulong masterTicket, out string ninjaSymbol)
        {
            ninjaSymbol = null;
            if (masterTicket == 0) return false;

            lock (orphanCloseLock)
            {
                foreach (KeyValuePair<string, PendingOrphanCloseRequest> kv in pendingOrphanCloseRequests)
                {
                    PendingOrphanCloseRequest request = kv.Value;
                    if (request != null && request.StaleMasterTickets != null && request.StaleMasterTickets.Contains(masterTicket))
                    {
                        ninjaSymbol = kv.Key;
                        return true;
                    }
                }
            }

            return false;
        }

        private bool IsOrphanClosePending(ulong masterTicket)
        {
            string ninjaSymbol;
            return TryGetPendingOrphanSymbolForTicket(masterTicket, out ninjaSymbol);
        }

        private void RemoveOrphanClosePending(string ninjaSymbol)
        {
            if (string.IsNullOrEmpty(ninjaSymbol)) return;

            List<ulong> staleTickets = new List<ulong>();
            lock (orphanCloseLock)
            {
                PendingOrphanCloseRequest request;
                if (pendingOrphanCloseRequests.TryGetValue(ninjaSymbol, out request) && request != null && request.StaleMasterTickets != null)
                    staleTickets = request.StaleMasterTickets.ToList();

                pendingOrphanCloseRequests.Remove(ninjaSymbol);
            }

            if (staleTickets.Count > 0)
            {
                lock (pendingCloseMasterTickets)
                {
                    foreach (ulong ticket in staleTickets)
                        pendingCloseMasterTickets.Remove(ticket);
                }
            }
        }

        private void ScheduleOrphanCloseRetry(string ninjaSymbol, string reason)
        {
            if (string.IsNullOrEmpty(ninjaSymbol)) return;

            lock (orphanCloseLock)
            {
                PendingOrphanCloseRequest request;
                if (!pendingOrphanCloseRequests.TryGetValue(ninjaSymbol, out request) || request == null)
                    return;

                // SYNC에서 같은 심볼의 master-live mapping이 확인된 뒤에는 절대로 orphan flatten을 재시도하지 않는다.
                if (request.BlockedByMasterLive)
                    return;

                request.CloseOrder = null;
                request.CloseSubmitted = false;
                request.CloseFilledAwaitingFlat = false;
                request.LastCloseFillTicks = 0;
                request.RetryNotBeforeTicks = DateTime.UtcNow.Ticks + TimeSpan.FromSeconds(1).Ticks;
            }

            PrintLog($"⚠️ [고아 심볼청산 재시도 대기] {ninjaSymbol} | {reason}");
        }

        private void BlockOrAbortOrphanCloseForMasterLive(string ninjaSymbol, string reason)
        {
            if (string.IsNullOrEmpty(ninjaSymbol)) return;

            bool removeImmediately = false;
            bool closeAlreadySubmitted = false;

            lock (orphanCloseLock)
            {
                PendingOrphanCloseRequest request;
                if (!pendingOrphanCloseRequests.TryGetValue(ninjaSymbol, out request) || request == null)
                    return;

                request.BlockedByMasterLive = true;
                closeAlreadySubmitted = request.CloseSubmitted;
                removeImmediately = !request.CloseSubmitted;
            }

            if (removeImmediately)
            {
                RemoveOrphanClosePending(ninjaSymbol);
                PrintLog($"🛑 [고아 심볼청산 차단] {ninjaSymbol} | master-live mapping 존재 | 시장가 제출 전 pending 제거 | {reason}");
            }
            else if (closeAlreadySubmitted)
            {
                PrintLog($"🚨 [고아 심볼청산 신규제출 차단] {ninjaSymbol} | master-live mapping이 확인되어 추가 orphan-close는 금지합니다. 이미 제출된 시장가 주문의 terminal 이벤트만 추적합니다. | {reason}");
            }
        }

        private void BeginOrphanClose(string ninjaSymbol, List<CopyPositionMap> staleMaps)
        {
            if (targetAccount == null || string.IsNullOrEmpty(ninjaSymbol) || staleMaps == null) return;

            List<ulong> staleTickets = staleMaps
                .Where(m => m != null && m.MasterTicket != 0 && string.Equals(m.NinjaSymbol, ninjaSymbol, StringComparison.OrdinalIgnoreCase))
                .Select(m => m.MasterTicket)
                .Distinct()
                .ToList();

            if (staleTickets.Count == 0) return;

            bool created = false;
            lock (orphanCloseLock)
            {
                PendingOrphanCloseRequest request;
                if (!pendingOrphanCloseRequests.TryGetValue(ninjaSymbol, out request) || request == null)
                {
                    request = new PendingOrphanCloseRequest
                    {
                        NinjaSymbol = ninjaSymbol,
                        StaleMasterTickets = staleTickets.ToList(),
                        TrackingMasterTicket = staleTickets[0],
                        CloseOrder = null,
                        CloseSubmitted = false,
                        CloseFilledAwaitingFlat = false,
                        BlockedByMasterLive = false,
                        LastProtectionFillTicks = 0,
                        LastCloseFillTicks = 0,
                        RetryNotBeforeTicks = 0
                    };
                    pendingOrphanCloseRequests[ninjaSymbol] = request;
                    created = true;
                }
                else
                {
                    // 같은 심볼에 새 stale Ticket이 추가되면 한 상태기계가 전체 stale 집합을 소유한다.
                    request.StaleMasterTickets = staleTickets.ToList();
                    if (!request.StaleMasterTickets.Contains(request.TrackingMasterTicket))
                        request.TrackingMasterTicket = request.StaleMasterTickets[0];
                    request.BlockedByMasterLive = false;
                }
            }

            lock (pendingCloseMasterTickets)
            {
                foreach (ulong ticket in staleTickets)
                    pendingCloseMasterTickets.Add(ticket);
            }

            // 이 심볼의 mapping이 전부 orphan으로 확정된 뒤에만 stale Ticket들의 보호교체를 중단한다.
            foreach (ulong ticket in staleTickets)
                AbortPendingProtectionReplacement(ticket, "같은 NinjaSymbol의 mapping이 전부 orphan으로 확정되어 심볼 단위 청산이 우선됩니다.");

            if (created)
            {
                PrintLog($"🚨 [고아 심볼 확정] {ninjaSymbol} | stale Tickets={string.Join(",", staleTickets)} | Ticket별 flatten 금지 / 실제 Ninja net Position 기준 정리 시작");
            }

            TryAdvanceOrphanClose(ninjaSymbol);
        }

        private bool R8OrphanQuantityMatches(PendingOrphanCloseRequest request,
            List<CopyPositionMap> maps, Position actual)
        {
            if (request == null || maps == null || maps.Any(m => m.QuantityFault)) return false;
            long owned = maps.Sum(m => (long)Math.Max(0, m.CurrentContracts));
            long expected = owned - request.AccountedOrphanFilled;
            int actualQuantity = actual == null || actual.MarketPosition == MarketPosition.Flat ? 0 : actual.Quantity;
            CopyPositionMap first = maps.FirstOrDefault(m => m.CurrentContracts > 0);
            bool sameDirection = first == null || maps.All(m => m.CurrentContracts <= 0 || m.Direction == first.Direction);
            bool directionMatches = actualQuantity == 0 || (first != null && actual != null &&
                actual.MarketPosition == (first.Direction == "BUY" ? MarketPosition.Long : MarketPosition.Short));
            if (expected >= 0 && expected == actualQuantity && sameDirection && directionMatches) return true;
            PrintLog($"[ORPHAN_QUANTITY_MISMATCH] {request.NinjaSymbol} | mapped={owned} | orphanFilled={request.AccountedOrphanFilled} | actual={actualQuantity} | 기존 보호 취소·추가 시장가 제출 보류");
            return false;
        }

        private void TryAdvanceOrphanClose(string ninjaSymbol)
        {
            if (targetAccount == null || string.IsNullOrEmpty(ninjaSymbol)) return;

            PendingOrphanCloseRequest request = null;
            lock (orphanCloseLock)
            {
                if (!pendingOrphanCloseRequests.TryGetValue(ninjaSymbol, out request) || request == null)
                    return;
            }

            if (request.BlockedByMasterLive)
                return;

            long nowTicks = DateTime.UtcNow.Ticks;
            if (request.RetryNotBeforeTicks > nowTicks)
                return;

            if (request.LastProtectionFillTicks > 0 &&
                nowTicks - request.LastProtectionFillTicks < TimeSpan.FromMilliseconds(500).Ticks)
            {
                return;
            }

            if (request.CloseFilledAwaitingFlat && request.LastCloseFillTicks > 0 &&
                nowTicks - request.LastCloseFillTicks < TimeSpan.FromMilliseconds(500).Ticks)
            {
                return;
            }

            List<ulong> requestedStaleTickets;
            lock (orphanCloseLock)
            {
                PendingOrphanCloseRequest latest;
                if (!pendingOrphanCloseRequests.TryGetValue(ninjaSymbol, out latest) || latest == null) return;
                requestedStaleTickets = latest.StaleMasterTickets == null ? new List<ulong>() : latest.StaleMasterTickets.ToList();
                request = latest;
            }

            // 현재 ticketMappings에서 이 심볼의 mapping 전체를 다시 스냅샷한다.
            // request에 없는 같은 심볼 mapping이 하나라도 있으면 master-live 가능성이 있으므로 전량 flatten을 절대 진행하지 않는다.
            List<CopyPositionMap> currentSymbolMaps;
            lock (ticketMappings)
            {
                currentSymbolMaps = ticketMappings.Values
                    .Where(m => m != null && string.Equals(m.NinjaSymbol, ninjaSymbol, StringComparison.OrdinalIgnoreCase))
                    .ToList();
            }

            List<ulong> activeStaleTickets = currentSymbolMaps
                .Where(m => requestedStaleTickets.Contains(m.MasterTicket))
                .Select(m => m.MasterTicket)
                .Distinct()
                .ToList();

            if (activeStaleTickets.Count == 0)
            {
                RemoveOrphanClosePending(ninjaSymbol);
                PrintLog($"🧹 [고아 심볼청산 종료] {ninjaSymbol} | stale mapping이 더 이상 없어 pending 상태만 정리합니다.");
                return;
            }

            List<ulong> nonOrphanTickets = currentSymbolMaps
                .Select(m => m.MasterTicket)
                .Where(t => !activeStaleTickets.Contains(t))
                .Distinct()
                .ToList();

            if (nonOrphanTickets.Count > 0)
            {
                PrintLog($"🛑 [고아 심볼청산 차단] {ninjaSymbol} | orphan 집합 외 동일 심볼 mapping 존재: {string.Join(",", nonOrphanTickets)} | 전량 flatten 금지");
                return;
            }

            // 부분 진입 중인 stale Ticket은 잔여 진입 종료가 먼저다.
            foreach (ulong ticket in activeStaleTickets)
            {
                if (HasOutstandingEntry(ticket))
                {
                    RequestEntryCancel(ticket);
                    return;
                }
            }
            // 아직 첫 체결 전인 다른 티켓도 같은 심볼의 살아 있는 진입으로 간주한다.
            lock (pendingEntries)
            {
                if (pendingEntries.Values.Any(p => p != null &&
                    string.Equals(p.NinjaSymbol, ninjaSymbol, StringComparison.OrdinalIgnoreCase) &&
                    !activeStaleTickets.Contains(p.MasterTicket))) return;
            }

            RefreshTicketExitQuantities();
            Position beforeCancel = PositionSnapshot().FirstOrDefault(p => p.Instrument.FullName == ninjaSymbol);
            if ((!request.CloseSubmitted || request.CloseFilledAwaitingFlat) &&
                !R8OrphanQuantityMatches(request, currentSymbolMaps, beforeCancel)) return;

            // 1) 같은 심볼의 모든 stale Ticket SL/TP를 취소하고 2) 전부 terminal일 때만 다음 단계로 간다.
            bool hasOutstandingProtection = false;
            foreach (ulong ticket in activeStaleTickets)
            {
                List<Order> outstanding = GetOutstandingProtectionOrders(ticket);
                if (outstanding.Count > 0)
                {
                    hasOutstandingProtection = true;
                    RequestOutstandingProtectionCancels(ticket, "고아 심볼청산 전 stale Ticket 전체 SL/TP 종료 확인");
                }
            }

            if (hasOutstandingProtection)
                return;

            // 이미 심볼 시장가 주문 하나가 살아 있으면 두 번째 주문을 만들지 않는다.
            if (request.CloseSubmitted && !request.CloseFilledAwaitingFlat)
                return;

            Instrument inst = Instrument.GetInstrument(ninjaSymbol);
            if (inst == null)
            {
                ScheduleOrphanCloseRetry(ninjaSymbol, "Ninja 종목을 찾을 수 없습니다: " + ninjaSymbol);
                return;
            }

            // [고아 핵심] 모든 관련 보호주문 terminal 확인이 끝난 뒤 실제 Ninja Position을 이 시점에 한 번 읽는다.
            Position pos = PositionSnapshot().FirstOrDefault(p => p.Instrument.FullName == inst.FullName);

            // 시장가 체결 후에도 mapping 삭제는 실제 Flat 확인 뒤에만 한다.
            if (pos == null || pos.MarketPosition == MarketPosition.Flat || pos.Quantity <= 0)
            {
                foreach (var m in currentSymbolMaps) if (!risk.CanFinalize(m.MasterTicket, m.InitialContracts)) return;
                foreach (var m in currentSymbolMaps) if (!risk.Finalize(m.MasterTicket, m.InitialContracts)) return;
                RemoveOrphanClosePending(ninjaSymbol);

                lock (ticketMappings)
                {
                    foreach (ulong ticket in activeStaleTickets)
                    {
                        CopyPositionMap staleMap;
                        if (ticketMappings.TryGetValue(ticket, out staleMap) && staleMap != null &&
                            string.Equals(staleMap.NinjaSymbol, ninjaSymbol, StringComparison.OrdinalIgnoreCase))
                        {
                            ticketMappings.Remove(ticket);
                        }
                    }
                }

                foreach (ulong ticket in activeStaleTickets)
                    ScheduleTrackedOrdersForTicketCleanup(ticket);

                PrintLog($"✅ [고아 심볼 정리 완료] {ninjaSymbol} | stale Tickets={string.Join(",", activeStaleTickets)} | 모든 SL/TP terminal + 실제 Ninja Position Flat 확인 후 stale mappings 일괄 제거");
                return;
            }

            // 이전 orphan-close가 Filled였지만 실제 잔여가 남으면, 방금 읽은 동일 Position 값으로 다음 1개 주문을 준비한다.
            if (request.CloseFilledAwaitingFlat)
            {
                lock (orphanCloseLock)
                {
                    PendingOrphanCloseRequest latest;
                    if (!pendingOrphanCloseRequests.TryGetValue(ninjaSymbol, out latest) || latest == null || latest.BlockedByMasterLive)
                        return;

                    latest.CloseOrder = null;
                    latest.CloseSubmitted = false;
                    latest.CloseFilledAwaitingFlat = false;
                    latest.LastCloseFillTicks = 0;
                    request = latest;
                }

                PrintLog($"🔄 [고아 심볼 잔여 확인] {ninjaSymbol} | 실제 {pos.MarketPosition} {pos.Quantity}계약 잔존 | 동일 심볼 시장가 주문 1개 원칙으로 재청산");
            }

            OrderAction closeAction;
            if (pos.MarketPosition == MarketPosition.Long)
                closeAction = OrderAction.Sell;
            else if (pos.MarketPosition == MarketPosition.Short)
                closeAction = OrderAction.BuyToCover;
            else
                return;

            int closeQty = pos.Quantity;
            if (closeQty <= 0) return;

            ulong trackingTicket = activeStaleTickets.Contains(request.TrackingMasterTicket)
                ? request.TrackingMasterTicket
                : activeStaleTickets[0];

            Order closeOrder = null;
            bool submissionAttempted = false;
            try
            {
                closeOrder = targetAccount.CreateOrder(
                    inst,
                    closeAction,
                    OrderType.Market,
                    OrderEntry.Automated,
                    TimeInForce.Day,
                    closeQty,
                    0,
                    0,
                    "",
                    "",
                    Core.Globals.MaxDate,
                    null);

                if (closeOrder == null)
                {
                    ScheduleOrphanCloseRetry(ninjaSymbol, "실제 포지션 시장가 청산 주문 객체 생성 실패");
                    return;
                }

                // 심볼당 시장가 orphan-close Order는 이 객체 하나만 등록한다.
                RegisterTrackedOrder(closeOrder, trackingTicket, TrackedOrderRole.OrphanClose);
                lock (orphanCloseLock)
                {
                    PendingOrphanCloseRequest latest;
                    if (!pendingOrphanCloseRequests.TryGetValue(ninjaSymbol, out latest) || latest == null || latest.BlockedByMasterLive)
                    {
                        UnregisterTrackedOrder(closeOrder);
                        return;
                    }

                    latest.TrackingMasterTicket = trackingTicket;
                    latest.CloseOrder = closeOrder;
                    latest.CloseSubmitted = true;
                    latest.CloseFilledAwaitingFlat = false;
                    latest.LastCloseFillTicks = 0;
                    latest.RetryNotBeforeTicks = 0;
                }

                submissionAttempted = true;
                targetAccount.Submit(new[] { closeOrder });
                PrintLog($"📤 [고아 심볼 실제포지션 청산] {ninjaSymbol} | stale Tickets={string.Join(",", activeStaleTickets)} | 실제 {pos.MarketPosition} -> {closeAction} | Position.Quantity={closeQty} | 심볼당 시장가 1개");
            }
            catch (Exception ex)
            {
                if (submissionAttempted)
                    PrintLog($"🚨 [고아 청산 접수 확인 대기] {ninjaSymbol} | 기존 주문 추적 유지 / 중복 제출 금지 | {ex.Message}");
                else
                {
                    UnregisterTrackedOrder(closeOrder);
                    ScheduleOrphanCloseRetry(ninjaSymbol, "시장가 청산 생성 예외: " + ex.Message);
                }
            }
        }

        private void CheckPendingOrphanCloseRequests()
        {
            List<string> pendingSymbols;
            lock (orphanCloseLock)
            {
                pendingSymbols = pendingOrphanCloseRequests.Keys.ToList();
            }

            foreach (string ninjaSymbol in pendingSymbols)
                TryAdvanceOrphanClose(ninjaSymbol);
        }

        // [OCO State Machine] 기존 보호주문을 Cancel 요청하고, 실제 종료 확인은 OrderUpdate/Watchdog에서 이어서 처리한다.
        private bool ReplaceProtectionSet(CopyPositionMap map, Instrument inst, double slPrice, double tpPrice, string reason)
        {
            if (targetAccount == null || map == null || inst == null || map.CurrentContracts <= 0 || slPrice <= 0.0)
                return false;

            lock (protectionReplacementLock)
            {
                PendingProtectionReplacement pending;
                if (!pendingProtectionReplacements.TryGetValue(map.MasterTicket, out pending))
                {
                    pending = new PendingProtectionReplacement { MasterTicket = map.MasterTicket };
                    pendingProtectionReplacements[map.MasterTicket] = pending;
                }

                // 교체 중 MODIFY/SYNC가 추가로 오면 가장 최신 목표값으로 덮어쓴다.
                pending.NinjaSymbol = inst.FullName;
                pending.SlPrice = slPrice;
                pending.TpPrice = tpPrice;
                pending.Quantity = map.CurrentContracts;
                pending.Reason = reason;
                pending.RequestedTicks = DateTime.UtcNow.Ticks;

                map.CurrentSlPrice = slPrice;
                map.CurrentTpPrice = tpPrice;
                map.ProtectionReplaceInProgress = true;
                map.ProtectionReplaceGuardUntilTicks = 0;
            }

            PrintLog($"🔁 [OCO 교체 예약] MT5 Ticket: {map.MasterTicket} | {map.CurrentContracts}계약 | SL: {slPrice} | TP: {tpPrice} | {reason}");

            // 실제 수량과 방향을 검증하는 TryComplete 경로에서만 취소한다.
            TryCompleteProtectionReplacement(map.MasterTicket);
            return true;
        }

        // 모든 이전 보호주문이 실제 terminal 상태가 된 경우에만 새 OCO 세트를 제출한다.
        private void TryCompleteProtectionReplacement(ulong masterTicket)
        {
            if (targetAccount == null || masterTicket == 0) return;
            PendingProtectionReplacement pending;
            lock (protectionReplacementLock)
                if (!pendingProtectionReplacements.TryGetValue(masterTicket, out pending)) return;

            RefreshTicketExitQuantities();
            bool closing;
            lock (pendingCloseMasterTickets) closing = pendingCloseMasterTickets.Contains(masterTicket);
            // 진입 취소 완료를 기다리는 동안에는 현재 체결된 진입 물량의 보호를 허용한다.
            if (closing && (IsOrphanClosePending(masterTicket) ||
                (!HasOutstandingEntry(masterTicket) && !R8RecoveringProtection(masterTicket))))
            {
                AbortPendingProtectionReplacement(masterTicket, "청산 진행을 위해 보호 재생성 중단");
                return;
            }
            CopyPositionMap map;
            lock (ticketMappings) ticketMappings.TryGetValue(masterTicket, out map);
            if (map == null || map.QuantityFault || map.CurrentContracts <= 0)
            {
                AbortPendingProtectionReplacement(masterTicket, "티켓 잔량 없음 또는 수량 확인 필요");
                if (map != null && map.CurrentContracts == 0) TryFinalizeTicket(masterTicket);
                return;
            }
            Instrument inst = Instrument.GetInstrument(map.NinjaSymbol);
            if (inst == null) return;
            Position pos = PositionSnapshot().FirstOrDefault(p => p.Instrument.FullName == inst.FullName);
            MarketPosition expected = map.Direction == "BUY" ? MarketPosition.Long : MarketPosition.Short;
            // Position 반영이 늦으면 요청을 보존하여 Watchdog에서 다시 대조한다.
            if (pos == null || pos.MarketPosition != expected || pos.Quantity <= 0) return;
            int ticketQty;
            if (!TryGetTicketScopedQuantity(map, pos, "보호주문 교체 직전", out ticketQty)) return;
            if (ticketQty <= 0) { TryFinalizeTicket(masterTicket); return; }
            if (map.CurrentSlPrice <= 0.0) return;
            if (GetOutstandingProtectionOrders(masterTicket).Count > 0)
            {
                RequestOutstandingProtectionCancels(masterTicket, pending.Reason);
                return;
            }

            // 과거 SL/TP 체결 여부로 중단하지 않고, 모든 체결을 반영한 현재 잔량으로 새 세대를 만든다.
            lock (protectionReplacementLock)
            {
                pendingProtectionReplacements.Remove(masterTicket);
                map.SlOrder = null;
                map.TpOrder = null;
                map.ProtectionOcoId = "";
            }
            bool submitted = SubmitProtectionSet(map, inst, ticketQty, map.CurrentSlPrice,
                map.CurrentTpPrice, pending.Reason + " - 기존 보호주문 종료 확인 후");
            lock (protectionReplacementLock)
            {
                map.ProtectionReplaceInProgress = false;
                map.ProtectionReplaceGuardUntilTicks = DateTime.UtcNow.Ticks + protectionReplacementGuardTicks;
            }
            if (!submitted)
                PrintLog($"🚨 [보호주문 제출 확인 필요] MT5 Ticket: {masterTicket} | 잔여 {ticketQty}계약 | 추적 주문 상태와 다음 SYNC 확인");
        }

        // 주문 상태 이벤트가 누락되거나 Cancel 요청 시점에 ChangePending이었던 경우를 위한 저주기 보조 확인.
        private void CheckPendingProtectionReplacements()
        {
            List<ulong> pendingTickets;
            lock (protectionReplacementLock)
            {
                pendingTickets = pendingProtectionReplacements.Keys.ToList();
            }

            foreach (ulong masterTicket in pendingTickets)
                TryCompleteProtectionReplacement(masterTicket);
        }

        // Account.OrderUpdate는 보호주문 Cancel 완료와 청산 Reject/Cancel을 모두 추적한다.
        private void OnOrderUpdate(object sender, OrderEventArgs e)
        {
            if (e == null || e.Order == null) return;
            QueueOrderObservation(e.Order, e.OrderState);
        }

        private void RouteSignal(string action, string mt5Symbol, double slDist, double masterEntryPrice, double masterSlPrice, double masterTpPrice, ulong ticket, double targetVol, double closedVol, string ticketsStr)
        {
            // [7단계] SYNC는 SYMBOL 필드가 없으므로 심볼 매핑보다 먼저 처리한다.
            if (action == "SYNC")
            {
                ProcessHeartbeatSync(ticketsStr);
                return;
            }

            // UI에 입력된 현재 월물 값 실시간 읽기
            string currentMnq = settings.Mnq;
            string currentMgc = settings.Mgc;
            string nt8Contract = MapSymbol(mt5Symbol, currentMnq, currentMgc);
            
            if (nt8Contract == "UNKNOWN") return;

            if (action == "CLOSE")
            {
                PrintLog($"🏁 [청산 신호 수신] MT5 Ticket: {ticket} | 종목: {nt8Contract} | 마스터 잔여: {targetVol} | 이번 청산: {closedVol}");
                CloseMappedPosition(ticket, nt8Contract, targetVol, closedVol);
                return;
            }

            // [5~6단계] 마스터 MODIFY를 티켓별 Ninja 보호주문에 동기화한다.
            if (action == "MODIFY")
            {
                ModifyMappedStop(ticket, nt8Contract, masterSlPrice);
                ModifyMappedTarget(ticket, nt8Contract, masterTpPrice);
                return;
            }

            if (action == "BUY" || action == "SELL")
            {
                string blockReason;
                if (!EntryAllowed(out blockReason)) { PrintLog("[ENTRY_BLOCKED] " + blockReason); return; }
                if (ticket == 0)
                {
                    PrintLog($"🚫 [진입 차단] 유효한 MT5 Ticket이 없습니다. {action} {nt8Contract}");
                    return;
                }

                if (IsMasterTicketAlreadyActive(ticket))
                {
                    PrintLog($"⚠️ [중복 진입 차단] MT5 Ticket: {ticket} | 이미 체결 완료 또는 체결 대기 중인 티켓입니다.");
                    return;
                }

                // [TRACE ONLY] 기존 계약수 계산에 들어가는 실제 입력값
                PrintLog($"🔎 [TRACE/ENTRY INPUT] MT5 Ticket: {ticket} | {action} {nt8Contract} | masterEntry={masterEntryPrice} | masterSL={masterSlPrice} | masterTP={masterTpPrice} | slDist={slDist}");

                int contracts = CalculateSafeContracts(nt8Contract, slDist);

                PrintLog($"🔎 [TRACE/RISK RESULT] MT5 Ticket: {ticket} | {action} {nt8Contract} | slDist={slDist} | calculatedContracts={contracts}");

                if (contracts <= 0)
                {
                    PrintLog($"🚫 [진입 차단] {action} {nt8Contract} | 손절폭 초과 또는 SL 누락 | SL거리: {slDist}pt");
                    return;
                }

                PrintLog($"🟢 [진입 승인] {action} {nt8Contract} | {contracts}계약 | SL거리: {slDist}pt (MT5 Ticket: {ticket})");
                ExecuteOrder(action, nt8Contract, contracts, slDist, masterEntryPrice, masterTpPrice, ticket);
            }
        }
        
        private bool R8CanSubmitEntry(string symbol, string direction)
        {
            if (targetAccount == null) return false;
            RefreshTicketExitQuantities();
            long owned = 0;
            lock (ticketMappings)
            {
                foreach (CopyPositionMap map in ticketMappings.Values)
                {
                    if (!string.Equals(map.NinjaSymbol, symbol, StringComparison.OrdinalIgnoreCase)) continue;
                    if (map.QuantityFault || (map.CurrentContracts > 0 && map.Direction != direction)) return false;
                    owned += Math.Max(0, map.CurrentContracts);
                    if (IsFullClosePending(map.MasterTicket) || IsOrphanClosePending(map.MasterTicket)) return false;
                }
            }
            lock (pendingEntries)
                if (pendingEntries.Values.Any(p => p != null &&
                    string.Equals(p.NinjaSymbol, symbol, StringComparison.OrdinalIgnoreCase) && p.Direction != direction)) return false;
            Position pos = PositionSnapshot().FirstOrDefault(p => p.Instrument.FullName == symbol);
            int actual = pos == null || pos.MarketPosition == MarketPosition.Flat ? 0 : pos.Quantity;
            MarketPosition expected = direction == "BUY" ? MarketPosition.Long : MarketPosition.Short;
            if (owned != actual || (actual > 0 && pos.MarketPosition != expected)) return false;
            // 자기 창이 추적하지 않는 살아 있는 주문과 같은 종목을 공유하지 않는다.
            foreach (Order order in OrderSnapshot())
            {
                if (order == null || order.Instrument == null || order.Instrument.FullName != symbol ||
                    IsProtectionOrderTerminal(order.OrderState)) continue;
                TrackedOrderInfo info;
                if (!TryGetTrackedOrderInfo(order, out info)) return false;
            }
            return true;
        }

        private void ExecuteOrder(string action, string contractSymbol, int quantity, double slDist, double masterEntryPrice, double masterTpPrice, ulong masterTicket)
        {
            if (targetAccount == null || !EntrySwitchOpen) return;

            Instrument inst = Instrument.GetInstrument(contractSymbol);
            if (inst == null)
            {
                PrintLog($"❌ [종목 오류] {contractSymbol}을 찾을 수 없습니다. 월물을 다시 확인해주세요.");
                return;
            }

            if (!R8CanSubmitEntry(inst.FullName, action))
            {
                PrintLog($"🚫 [진입 사전검증 차단] {contractSymbol} | 반대방향·미추적 주문 또는 실제/매핑 수량 불일치");
                return;
            }

            OrderAction entryAction = action == "BUY" ? OrderAction.Buy : OrderAction.SellShort;
            Order entryOrder = null;
            bool submissionAttempted = false;

            try
            {
                entryOrder = targetAccount.CreateOrder(inst, entryAction, OrderType.Market, OrderEntry.Automated, TimeInForce.Day, quantity, 0, 0, "", "", Core.Globals.MaxDate, null);

                if (entryOrder == null)
                {
                    PrintLog($"❌ [진입 주문 생성 실패] MT5 Ticket: {masterTicket} | Ninja 주문 객체가 생성되지 않았습니다.");
                    return;
                }

                // Submit 전에 Master Ticket 기준 대기정보를 먼저 보존한다.
                lock (pendingEntries)
                {
                    pendingEntries[masterTicket] = new PendingEntryInfo
                    {
                        MasterTicket = masterTicket,
                        SlDist = slDist,
                        MasterEntryPrice = masterEntryPrice,
                        MasterTpPrice = masterTpPrice,
                        NinjaSymbol = inst.FullName,
                        Direction = action,
                        Contracts = quantity,
                        EntryOrder = entryOrder
                    };
                }

                // Submit 내부에서 OrderUpdate가 즉시 발생해도 추적 가능하도록 발주 전에 Order 객체를 등록한다.
                RegisterTrackedOrder(entryOrder, masterTicket, TrackedOrderRole.Entry);
                TrackedOrderInfo entryTracking;
                if (TryGetTrackedOrderInfo(entryOrder, out entryTracking))
                {
                    lock (pendingEntries) entryTracking.EntryContext = pendingEntries[masterTicket];
                }

                // [TRACE ONLY] Ninja 시장가 진입 Submit 직전 값
                PrintLog($"🔎 [TRACE/NINJA ENTRY SUBMIT] MT5 Ticket: {masterTicket} | {action} {contractSymbol} | Qty={quantity} | slDist={slDist} | masterEntry={masterEntryPrice} | masterTP={masterTpPrice} | OrderName=<empty> | Tracking=OrderObject");
                if(!EntrySwitchOpen)
                {
                    UnregisterTrackedOrder(entryOrder);lock(pendingEntries)pendingEntries.Remove(masterTicket);
                    PrintLog("[ENTRY_BLOCKED] DISABLED_OR_SETTINGS_PENDING");return;
                }
                submissionAttempted = true;
                R6Record(masterTicket).Attempted = true;
                targetAccount.Submit(new[] { entryOrder });
            }
            catch (Exception ex)
            {
                if (!submissionAttempted)
                {
                    UnregisterTrackedOrder(entryOrder);
                    lock (pendingEntries) pendingEntries.Remove(masterTicket);
                }
                PrintLog($"⚠️ [진입 주문 오류] MT5 Ticket: {masterTicket} | 제출시도={submissionAttempted} / 제출시도 후 오류는 기존 주문 추적 유지 | {ex.Message}");
            }
        }


        // 브로커 콜백에서는 값만 복사한다. 모든 티켓 상태 변경은 신호 처리와 같은 Dispatcher에서 직렬 실행한다.
        // Submit/Cancel 내부의 동기 콜백도 큐에 넣어 현재 상태 전환에 재진입하지 않게 한다.
        private void QueueOrderObservation(Order order, OrderState state)
        {
            TrackedOrderInfo ignored;
            if (!TryGetTrackedOrderInfo(order, out ignored)) return;
            int filled = order.Filled;
            double average = order.AverageFillPrice;
            Dispatcher.InvokeAsync(() =>
            {
                try { ProcessOrderObservation(order, state, filled, average); }
                catch (Exception ex) { risk.DataFault = true; risk.Reason = "ORDER_EVENT_EXCEPTION"; PrintLog(ex.Message); }
            });
        }

        private void ProcessOrderObservation(Order order, OrderState state, int filled, double average)
        {
            TrackedOrderInfo tracking;
            // 계좌 변경/종료로 추적을 비운 뒤 도착한 큐 이벤트는 적용하지 않는다.
            if (targetAccount == null || !TryGetTrackedOrderInfo(order, out tracking)) return;
            if (order.Filled > filled)
            {
                filled = order.Filled;
                average = order.AverageFillPrice;
            }
            if (filled >= tracking.ObservedFilled)
            {
                tracking.ObservedFilled = filled;
                if (average > 0.0) tracking.ObservedAverageFillPrice = average;
            }
            if (IsProtectionOrderTerminal(order.OrderState)) state = order.OrderState;
            if (IsProtectionOrderTerminal(state))
            {
                if (!tracking.TerminalObserved || tracking.TerminalState != OrderState.Filled)
                    tracking.TerminalState = state;
                tracking.TerminalObserved = true;
            }

            RefreshTicketExitQuantities();
            ulong masterTicket = tracking.MasterTicket;
            int lifecycleDelta = Math.Max(0, tracking.ObservedFilled - tracking.LifecycleFilled);
            bool newFill = lifecycleDelta > 0;
            tracking.LifecycleFilled = Math.Max(tracking.LifecycleFilled, tracking.ObservedFilled);
            bool terminal = IsTrackedOrderTerminal(order);
            bool newTerminal = terminal && !tracking.TerminalHandled;
            if (terminal)
            {
                tracking.TerminalHandled = true;
                ScheduleTrackedOrderCleanup(order);
            }
            OrderState terminalState = tracking.TerminalObserved ? tracking.TerminalState : order.OrderState;

            CopyPositionMap map;
            lock (ticketMappings) ticketMappings.TryGetValue(masterTicket, out map);
            if (tracking.Role == TrackedOrderRole.Entry)
            {
                if (terminal)
                {
                    if (tracking.ObservedFilled == 0 && map == null) R6Record(masterTicket).NoOpen = true;
                    lock (pendingEntries) pendingEntries.Remove(masterTicket);
                }
                if (newFill)
                    PrintLog($"🔗 [진입 체결 반영] MT5 Ticket: {masterTicket} | 누적 {tracking.AppliedEntryFilled}계약 / 티켓 잔여 {(map != null ? map.CurrentContracts : 0)}계약");
                if (newFill && tracking.EntryContext != null && tracking.EntryContext.CloseAllRequested &&
                    !IsFullClosePending(masterTicket) && !IsOrphanClosePending(masterTicket))
                    QueueTicketClose(masterTicket, tracking.EntryContext.NinjaSymbol, true, 1.0);
                if (map != null) EnsureTicketProtection(map, "진입 부분/최종 체결량 보호");
                if (IsFullClosePending(masterTicket)) TryAdvanceFullClose(masterTicket);
                else if (map != null && map.CurrentContracts == 0) TryFinalizeTicket(masterTicket);
                return;
            }

            if (tracking.Role == TrackedOrderRole.PartialClose || tracking.Role == TrackedOrderRole.FullClose)
            {
                PendingFullCloseRequest request;
                lock (fullCloseLock) pendingFullCloseRequests.TryGetValue(masterTicket, out request);
                if (request == null || !object.ReferenceEquals(request.CloseOrder, order))
                {
                    // 이전 주문의 늦은 체결은 수량에만 반영하고 현재 청산 요청을 종료하지 않는다.
                    if (newFill && map != null)
                    {
                        EnsureTicketProtection(map, "이전 청산 주문의 지연 체결 반영");
                        TryFinalizeTicket(masterTicket);
                    }
                    return;
                }
                if (!newTerminal) return;
                request.CloseOrder = null;
                request.CloseSubmitted = false;
                if (terminalState == OrderState.Filled || request.Revision > request.SubmittedRevision)
                {
                    // 별도의 후속 CLOSE가 있으면 이미 체결된 수량을 제외한 목표 잔량까지 이어서 처리한다.
                    TryAdvanceFullClose(masterTicket);
                }
                else RecoverProtectionAfterCloseFailure(masterTicket, "청산 주문 " + terminalState, true);
                return;
            }

            // 기존 고아 심볼 청산의 Flat 확인/재시도 정책은 동일하게 유지한다.
            if (tracking.Role == TrackedOrderRole.OrphanClose)
            {
                string symbol = order.Instrument != null ? order.Instrument.FullName : null;
                if (string.IsNullOrEmpty(symbol)) TryGetPendingOrphanSymbolForTicket(masterTicket, out symbol);
                if (string.IsNullOrEmpty(symbol)) return;
                PendingOrphanCloseRequest orphan;
                lock (orphanCloseLock) pendingOrphanCloseRequests.TryGetValue(symbol, out orphan);
                if (orphan == null || !object.ReferenceEquals(orphan.CloseOrder, order)) return;
                orphan.AccountedOrphanFilled += lifecycleDelta;
                if (!newTerminal) return;
                ScheduleTrackedOrderCleanup(order);
                if (orphan.BlockedByMasterLive)
                {
                    RemoveOrphanClosePending(symbol);
                    return;
                }
                if (terminalState == OrderState.Filled)
                {
                    orphan.CloseFilledAwaitingFlat = true;
                    orphan.LastCloseFillTicks = DateTime.UtcNow.Ticks;
                    TryAdvanceOrphanClose(symbol);
                }
                else ScheduleOrphanCloseRetry(symbol, "고아 시장가 청산 주문 " + terminalState);
                return;
            }

            if (tracking.Role != TrackedOrderRole.StopLoss && tracking.Role != TrackedOrderRole.TakeProfit) return;
            if (newFill) RequestEntryCancel(masterTicket);
            string orphanSymbol;
            if (TryGetPendingOrphanSymbolForTicket(masterTicket, out orphanSymbol))
            {
                PendingOrphanCloseRequest orphan;
                lock (orphanCloseLock) pendingOrphanCloseRequests.TryGetValue(orphanSymbol, out orphan);
                if (newFill && orphan != null) orphan.LastProtectionFillTicks = DateTime.UtcNow.Ticks;
                TryAdvanceOrphanClose(orphanSymbol);
                return;
            }
            if (map != null && map.QuantityFault)
            {
                AbortPendingProtectionReplacement(masterTicket, "티켓 초과체결 수량 확인 필요");
                RequestOutstandingProtectionCancels(masterTicket, "초과체결 후 추가 보호체결 차단");
                return;
            }
            if (IsFullClosePending(masterTicket))
            {
                PendingFullCloseRequest request;
                lock (fullCloseLock) pendingFullCloseRequests.TryGetValue(masterTicket, out request);
                if (newFill && request != null) request.LastProtectionFillTicks = DateTime.UtcNow.Ticks;
                TryAdvanceFullClose(masterTicket);
                if (map != null && HasOutstandingEntry(masterTicket)) EnsureTicketProtection(map, "진입 잔량 취소 중 보호 유지");
                return;
            }
            if (map == null) return;
            if (map.CurrentContracts == 0)
            {
                TryFinalizeTicket(masterTicket);
                return;
            }
            bool replacing;
            lock (protectionReplacementLock) replacing = pendingProtectionReplacements.ContainsKey(masterTicket);
            if (replacing) TryCompleteProtectionReplacement(masterTicket);
            else if (newFill || (newTerminal && terminalState != OrderState.Rejected))
                EnsureTicketProtection(map, "보호주문 체결/종료 후 티켓 잔량 보호");
            else if (newTerminal && terminalState == OrderState.Rejected)
                PrintLog($"🚨 [보호주문 거절] MT5 Ticket: {masterTicket} | 잔여 {map.CurrentContracts}계약 | 다음 SYNC에서 보호상태 재확인");
        }

// R11 OnExecutionUpdate moved to isolated orchestration

        // [7단계] 마스터 SYNC heartbeat / 잔존 포지션 Watchdog
        private void ProcessHeartbeatSync(string activeTicketsStr)
        {
            Interlocked.Exchange(ref lastSyncReceivedTicks, DateTime.UtcNow.Ticks);

            if (syncDisconnectWarningShown)
            {
                syncDisconnectWarningShown = false;
                PrintLog("✅ [SYNC Watchdog 복구] 마스터 heartbeat 수신이 정상화되었습니다.");
            }

            if (!firstSyncLogged)
            {
                firstSyncLogged = true;
                PrintLog("🔄 [SYNC 연결 확인] 마스터 heartbeat 수신 시작");
            }

            if (string.IsNullOrWhiteSpace(activeTicketsStr))
            {
                PrintLog("⚠️ [SYNC 무시] TICKETS 값이 비어 있습니다.");
                return;
            }

            Dictionary<ulong, MasterSyncState> masterStates = new Dictionary<ulong, MasterSyncState>();
            bool explicitNone = string.Equals(activeTicketsStr, "NONE", StringComparison.OrdinalIgnoreCase);

            if (!explicitNone)
            {
                string[] pairs = activeTicketsStr.Split(',');
                bool parseError = false;

                foreach (string pair in pairs)
                {
                    string[] fields = pair.Split(':');
                    if (fields.Length < 2 || fields.Length > 3)
                    {
                        parseError = true;
                        break;
                    }

                    ulong masterTicket;
                    double masterSl;
                    double masterTp = 0.0;

                    if (!ulong.TryParse(fields[0], out masterTicket) || masterTicket == 0 ||
                        !double.TryParse(fields[1], System.Globalization.NumberStyles.AllowDecimalPoint, System.Globalization.CultureInfo.InvariantCulture, out masterSl) ||
                        (fields.Length == 3 && !double.TryParse(fields[2], System.Globalization.NumberStyles.AllowDecimalPoint, System.Globalization.CultureInfo.InvariantCulture, out masterTp)))
                    {
                        parseError = true;
                        break;
                    }

                    masterStates[masterTicket] = new MasterSyncState
                    {
                        SlPrice = masterSl,
                        TpPrice = masterTp
                    };
                }

                // SYNC 문자열이 손상됐을 때 빈 목록으로 오인해 Ninja 포지션을 청산하지 않도록 전체 SYNC를 폐기한다.
                if (parseError || masterStates.Count == 0)
                {
                    PrintLog($"⚠️ [SYNC 무시] TICKETS 파싱 실패: {activeTicketsStr}");
                    return;
                }
            }

            List<CopyPositionMap> mappedSnapshot;
            lock (ticketMappings)
            {
                mappedSnapshot = ticketMappings.Values.Where(m => m != null).ToList();
            }

            // [고아 Symbol 직렬화] 먼저 전체 mapping을 master-live / stale로 판정한다.
            // Ticket 하나를 보는 순간 바로 flatten을 시작하지 않는다. 같은 NinjaSymbol의 전체 mapping 상태가 판정 기준이다.
            List<CopyPositionMap> liveMappings = mappedSnapshot
                .Where(m => masterStates.ContainsKey(m.MasterTicket))
                .ToList();

            List<CopyPositionMap> staleMappings = mappedSnapshot
                .Where(m => !masterStates.ContainsKey(m.MasterTicket))
                .ToList();

            HashSet<string> liveSymbols = new HashSet<string>(
                liveMappings.Where(m => !string.IsNullOrEmpty(m.NinjaSymbol)).Select(m => m.NinjaSymbol),
                StringComparer.OrdinalIgnoreCase);

            Dictionary<string, List<CopyPositionMap>> staleBySymbol = staleMappings
                .Where(m => !string.IsNullOrEmpty(m.NinjaSymbol))
                .GroupBy(m => m.NinjaSymbol, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(g => g.Key, g => g.ToList(), StringComparer.OrdinalIgnoreCase);

            // 이미 고아청산 대기 중이던 심볼에 master-live mapping이 나타나면 신규 flatten을 즉시 금지한다.
            foreach (string liveSymbol in liveSymbols)
                BlockOrAbortOrphanCloseForMasterLive(liveSymbol, "현재 SYNC에 동일 NinjaSymbol master-live mapping 존재");

            // master-live mapping은 기존 보호주문 SYNC 로직을 그대로 사용한다.
            foreach (CopyPositionMap map in liveMappings)
            {
                MasterSyncState masterState;
                if (!masterStates.TryGetValue(map.MasterTicket, out masterState))
                    continue;

                lock (protectionReplacementLock)
                {
                    if (IsProtectionReplacementGuardActive(map))
                        continue;

                    SyncMappedProtectionFromHeartbeat(map, masterState);
                }
            }

            // stale mapping은 NinjaSymbol 단위로만 처리한다.
            foreach (KeyValuePair<string, List<CopyPositionMap>> group in staleBySymbol)
            {
                string ninjaSymbol = group.Key;
                List<CopyPositionMap> staleGroup = group.Value;

                // 같은 심볼에 master-live mapping이 하나라도 있으면 net Position 전량 flatten을 절대 하지 않는다.
                if (liveSymbols.Contains(ninjaSymbol))
                {
                    PrintLog($"🛑 [SYNC 고아 심볼청산 금지] {ninjaSymbol} | stale Tickets={string.Join(",", staleGroup.Select(m => m.MasterTicket))} | 동일 심볼 master-live mapping 존재");
                    continue;
                }

                // 기존 CLOSE/부분청산이 진행 중인 Ticket과 orphan 상태기계를 겹쳐 실행하지 않는다.
                // 현재 심볼 orphan 상태기계가 이미 소유한 pending Ticket은 예외로 계속 진행한다.
                bool hasOtherCloseInProgress = false;
                foreach (CopyPositionMap staleMap in staleGroup)
                {
                    bool ticketPending;
                    lock (pendingCloseMasterTickets)
                    {
                        ticketPending = pendingCloseMasterTickets.Contains(staleMap.MasterTicket);
                    }

                    if (ticketPending && !IsOrphanClosePending(staleMap.MasterTicket))
                    {
                        hasOtherCloseInProgress = true;
                        break;
                    }
                }

                if (hasOtherCloseInProgress)
                {
                    PrintLog($"⏳ [SYNC 고아 심볼청산 대기] {ninjaSymbol} | 동일 심볼 stale Ticket 중 기존 청산 진행 건이 있어 orphan flatten을 시작하지 않습니다.");
                    continue;
                }

                PrintLog($"🚨 [SYNC 고아 심볼 감지] {ninjaSymbol} | stale Tickets={string.Join(",", staleGroup.Select(m => m.MasterTicket))} | 동일 심볼 mapping 전부 orphan");
                BeginOrphanClose(ninjaSymbol, staleGroup);
            }
        }

        private void SyncMappedProtectionFromHeartbeat(CopyPositionMap map, MasterSyncState masterState)
        {
            if (targetAccount == null || map == null || map.CurrentContracts <= 0 || map.MasterEntryPrice <= 0.0)
                return;

            // [7단계 수정2] 전체청산이 진행 중인 티켓에는 SYNC가 보호주문을 다시 만들거나 교정하지 않는다.
            lock (pendingCloseMasterTickets)
            {
                if (pendingCloseMasterTickets.Contains(map.MasterTicket))
                    return;
            }

            Instrument inst = Instrument.GetInstrument(map.NinjaSymbol);
            if (inst == null) return;

            double tickSize = inst.MasterInstrument.TickSize;
            if (tickSize <= 0.0) tickSize = 0.0000001;

            // heartbeat 한 번에서 목표 TP를 먼저 계산해 SL 복구가 필요할 때 같은 새 OCO 세트로 복구한다.
            double targetTp = 0.0;
            if (masterState.TpPrice > 0.0)
            {
                targetTp = map.AverageFillPrice + (masterState.TpPrice - map.MasterEntryPrice);
                targetTp = inst.MasterInstrument.RoundToTickSize(targetTp);
                if (targetTp <= 0.0) targetTp = 0.0;
            }

            // SL: 기존 정책대로 마스터 SL=0은 Ninja 보호 SL을 삭제하지 않는다.
            if (masterState.SlPrice > 0.0)
            {
                double targetSl = map.AverageFillPrice + (masterState.SlPrice - map.MasterEntryPrice);
                targetSl = inst.MasterInstrument.RoundToTickSize(targetSl);

                Order anySlOrder = map.SlOrder;

                Order activeSlOrder = (anySlOrder != null &&
                    (anySlOrder.OrderState == OrderState.Working || anySlOrder.OrderState == OrderState.Accepted))
                    ? anySlOrder : null;

                if (activeSlOrder != null)
                {
                    if (Math.Abs(activeSlOrder.StopPrice - targetSl) >= tickSize * 0.5)
                    {
                        PrintLog($"🧭 [SYNC SL 교정] MT5 Ticket: {map.MasterTicket} | 누락된 SL 변경을 재동기화합니다.");
                        ModifyMappedStop(map.MasterTicket, map.NinjaSymbol, masterState.SlPrice);
                    }
                }
                else
                {
                    bool terminalOrMissing = anySlOrder == null ||
                        anySlOrder.OrderState == OrderState.Cancelled ||
                        anySlOrder.OrderState == OrderState.Rejected ||
                        anySlOrder.OrderState == OrderState.Filled;

                    if (terminalOrMissing && targetSl > 0.0)
                        RecreateMappedStopFromSync(map, inst, targetSl, targetTp);
                }
            }

            // TP: heartbeat의 TP=0도 기존 6단계 정책대로 Ninja TP 삭제로 반영한다.
            Order anyTpOrder = map.TpOrder;

            Order activeTpOrder = (anyTpOrder != null &&
                (anyTpOrder.OrderState == OrderState.Working || anyTpOrder.OrderState == OrderState.Accepted))
                ? anyTpOrder : null;

            if (masterState.TpPrice <= 0.0)
            {
                if (activeTpOrder != null || map.CurrentTpPrice > 0.0)
                {
                    PrintLog($"🧭 [SYNC TP 교정] MT5 Ticket: {map.MasterTicket} | 마스터 TP 삭제 상태를 재동기화합니다.");
                    ModifyMappedTarget(map.MasterTicket, map.NinjaSymbol, 0.0);
                }
                return;
            }

            if (targetTp <= 0.0) return;

            if (activeTpOrder != null)
            {
                if (Math.Abs(activeTpOrder.LimitPrice - targetTp) >= tickSize * 0.5)
                {
                    PrintLog($"🧭 [SYNC TP 교정] MT5 Ticket: {map.MasterTicket} | 누락된 TP 변경을 재동기화합니다.");
                    ModifyMappedTarget(map.MasterTicket, map.NinjaSymbol, masterState.TpPrice);
                }
            }
            else
            {
                bool terminalOrMissing = anyTpOrder == null ||
                    anyTpOrder.OrderState == OrderState.Cancelled ||
                    anyTpOrder.OrderState == OrderState.Rejected ||
                    anyTpOrder.OrderState == OrderState.Filled;

                if (terminalOrMissing)
                {
                    PrintLog($"🧭 [SYNC TP 복구] MT5 Ticket: {map.MasterTicket} | 활성 TP가 없어 OCO 보호세트 재생성을 요청합니다.");
                    ModifyMappedTarget(map.MasterTicket, map.NinjaSymbol, masterState.TpPrice);
                }
            }
        }

        private void RecreateMappedStopFromSync(CopyPositionMap map, Instrument inst, double targetSl, double targetTp)
        {
            if (targetAccount == null || map == null || inst == null || map.CurrentContracts <= 0 || targetSl <= 0.0)
                return;

            map.CurrentSlPrice = targetSl;
            map.CurrentTpPrice = targetTp;

            // [OCO] SL이 유실됐다면 현재 heartbeat의 TP 상태까지 포함해 보호주문 전체를 새 세대로 복구한다.
            ReplaceProtectionSet(map, inst, targetSl, targetTp, "SYNC SL 복구");
        }

        private void CheckSyncWatchdog()
        {
            long lastSyncTicks = Interlocked.Read(ref lastSyncReceivedTicks);
            if (lastSyncTicks <= 0) return;

            long nowTicks = DateTime.UtcNow.Ticks;
            long lastCheckTicks = Interlocked.Read(ref lastSyncWatchdogCheckTicks);

            if (lastCheckTicks > 0 && nowTicks - lastCheckTicks < TimeSpan.FromMilliseconds(500).Ticks)
                return;

            Interlocked.Exchange(ref lastSyncWatchdogCheckTicks, nowTicks);

            if (nowTicks - lastSyncTicks > TimeSpan.FromSeconds(5).Ticks && !syncDisconnectWarningShown)
            {
                syncDisconnectWarningShown = true;
                PrintLog("⚠️ [SYNC Watchdog] 마스터 heartbeat가 5초 이상 수신되지 않았습니다.");
            }
        }

        // [5단계] Master Ticket 기준 SL MODIFY 동기화
        private void ModifyMappedStop(ulong masterTicket, string fallbackContractSymbol, double masterSlPrice)
        {
            if (targetAccount == null) return;

            if (masterTicket == 0)
            {
                PrintLog($"⚠️ [SL 동기화 차단] 유효한 MT5 Ticket이 없습니다. 종목: {fallbackContractSymbol}");
                return;
            }

            // 마스터에서 SL=0은 SL 삭제 상태다. 현재 단계에서는 보호 SL 삭제를 복제하지 않는다.
            if (masterSlPrice <= 0.0)
            {
                PrintLog($"ℹ️ [SL 동기화 보류] MT5 Ticket: {masterTicket} | 마스터 SL이 0이므로 Ninja 보호 SL은 유지합니다.");
                return;
            }

            CopyPositionMap map = null;
            lock (ticketMappings)
            {
                ticketMappings.TryGetValue(masterTicket, out map);
            }

            if (map == null)
            {
                PrintLog($"⚠️ [SL 동기화 차단] MT5 Ticket: {masterTicket} | Ninja 매핑을 찾을 수 없습니다.");
                return;
            }

            if (map.MasterEntryPrice <= 0.0)
            {
                PrintLog($"⚠️ [SL 동기화 차단] MT5 Ticket: {masterTicket} | 마스터 진입가 정보가 없습니다.");
                return;
            }

            Instrument inst = Instrument.GetInstrument(map.NinjaSymbol);
            if (inst == null)
            {
                PrintLog($"❌ [SL 동기화 오류] MT5 Ticket: {masterTicket} | Ninja 종목을 찾을 수 없습니다: {map.NinjaSymbol}");
                return;
            }

            // MT5 절대가격을 Ninja에 그대로 쓰지 않는다.
            // 마스터 진입가 대비 SL의 부호 있는 거리만 추출해 Ninja 실제 평균체결가에 동일하게 적용한다.
            double signedOffset = masterSlPrice - map.MasterEntryPrice;
            double targetNinjaSl = map.AverageFillPrice + signedOffset;
            targetNinjaSl = inst.MasterInstrument.RoundToTickSize(targetNinjaSl);

            if (targetNinjaSl <= 0.0)
            {
                PrintLog($"⚠️ [SL 동기화 차단] MT5 Ticket: {masterTicket} | 계산된 Ninja SL 가격이 유효하지 않습니다: {targetNinjaSl}");
                return;
            }

            // 부분청산 체결과 MODIFY가 근접해서 들어오는 경우, SL 교체가 진행 중이어도
            // 원하는 최신 SL 가격을 먼저 매핑에 저장해 이후 재생성 SL이 이 가격을 사용하게 한다.
            double oldSl = map.CurrentSlPrice;
            map.CurrentSlPrice = targetNinjaSl;

            lock (protectionReplacementLock)
            {
                if (IsProtectionReplacementGuardActive(map))
                {
                    PrintLog($"ℹ️ [SL 동기화 대기] MT5 Ticket: {masterTicket} | 보호주문 교체 중이므로 최신 목표 SL {targetNinjaSl}만 보존합니다.");
                    return;
                }
            }

            Order mappedSlOrder = (map.SlOrder != null &&
                (map.SlOrder.OrderState == OrderState.Working || map.SlOrder.OrderState == OrderState.Accepted))
                ? map.SlOrder : null;

            if (mappedSlOrder == null)
            {
                PrintLog($"ℹ️ [SL 동기화 대기] MT5 Ticket: {masterTicket} | 활성 Ninja SL을 찾지 못했습니다. 최신 목표 SL {targetNinjaSl}을 매핑에 보존합니다.");
                return;
            }

            double tickSize = inst.MasterInstrument.TickSize;
            if (tickSize <= 0.0) tickSize = 0.0000001;

            if (Math.Abs(mappedSlOrder.StopPrice - targetNinjaSl) < tickSize * 0.5)
            {
                PrintLog($"ℹ️ [SL 동기화 동일] MT5 Ticket: {masterTicket} | Ninja SL이 이미 {targetNinjaSl}입니다.");
                return;
            }

            try
            {
                mappedSlOrder.StopPriceChanged = targetNinjaSl;
                targetAccount.Change(new[] { mappedSlOrder });
                PrintLog($"🔄 [SL 동기화 요청] MT5 Ticket: {masterTicket} | Ninja SL {oldSl} → {targetNinjaSl} | 마스터 SL: {masterSlPrice}");
            }
            catch (Exception ex)
            {
                // 변경 요청 자체가 실패하면 매핑 가격도 실제 기존 SL로 되돌린다.
                map.CurrentSlPrice = oldSl;
                PrintLog($"⚠️ [SL 동기화 오류] MT5 Ticket: {masterTicket} | {ex.Message}");
            }
        }

        // [6단계] Master Ticket 기준 TP MODIFY 동기화
        private void ModifyMappedTarget(ulong masterTicket, string fallbackContractSymbol, double masterTpPrice)
        {
            if (targetAccount == null) return;

            if (masterTicket == 0)
            {
                PrintLog($"⚠️ [TP 동기화 차단] 유효한 MT5 Ticket이 없습니다. 종목: {fallbackContractSymbol}");
                return;
            }

            CopyPositionMap map = null;
            lock (ticketMappings)
            {
                ticketMappings.TryGetValue(masterTicket, out map);
            }

            if (map == null)
            {
                PrintLog($"⚠️ [TP 동기화 차단] MT5 Ticket: {masterTicket} | Ninja 매핑을 찾을 수 없습니다.");
                return;
            }

            Instrument inst = Instrument.GetInstrument(map.NinjaSymbol);
            if (inst == null)
            {
                PrintLog($"❌ [TP 동기화 오류] MT5 Ticket: {masterTicket} | Ninja 종목을 찾을 수 없습니다: {map.NinjaSymbol}");
                return;
            }

            // 마스터 TP 삭제(TP=0)도 Ninja에 동일하게 반영한다.
            if (masterTpPrice <= 0.0)
            {
                map.CurrentTpPrice = 0.0;

                lock (protectionReplacementLock)
                {
                    if (IsProtectionReplacementGuardActive(map))
                    {
                        PrintLog($"ℹ️ [TP 동기화 대기] MT5 Ticket: {masterTicket} | 보호주문 교체 중이므로 TP 삭제 상태만 보존합니다.");
                        return;
                    }
                }

                // [OCO] OCO TP 하나만 Cancel하면 짝 SL도 자동 취소된다.
                // 따라서 TP 삭제는 기존 보호세트 전체를 정리하고 SL 단독 세대로 재구성한다.
                if (map.CurrentSlPrice > 0.0 && map.CurrentContracts > 0)
                {
                    ReplaceProtectionSet(map, inst, map.CurrentSlPrice, 0.0, "마스터 TP 삭제 - SL 단독 전환");
                }
                else
                {
                    PrintLog($"⚠️ [TP 동기화 삭제 차단] MT5 Ticket: {masterTicket} | 유효한 SL 가격 또는 계약수가 없어 OCO 보호세트를 재구성할 수 없습니다.");
                }

                return;
            }

            if (map.MasterEntryPrice <= 0.0)
            {
                PrintLog($"⚠️ [TP 동기화 차단] MT5 Ticket: {masterTicket} | 마스터 진입가 정보가 없습니다.");
                return;
            }

            // MT5 절대 TP 가격 대신 진입가 대비 상대거리만 Ninja 실제 체결가에 적용한다.
            double signedOffset = masterTpPrice - map.MasterEntryPrice;
            double targetNinjaTp = map.AverageFillPrice + signedOffset;
            targetNinjaTp = inst.MasterInstrument.RoundToTickSize(targetNinjaTp);

            if (targetNinjaTp <= 0.0)
            {
                PrintLog($"⚠️ [TP 동기화 차단] MT5 Ticket: {masterTicket} | 계산된 Ninja TP 가격이 유효하지 않습니다: {targetNinjaTp}");
                return;
            }

            double oldTp = map.CurrentTpPrice;
            map.CurrentTpPrice = targetNinjaTp;

            lock (protectionReplacementLock)
            {
                if (IsProtectionReplacementGuardActive(map))
                {
                    PrintLog($"ℹ️ [TP 동기화 대기] MT5 Ticket: {masterTicket} | 보호주문 교체 중이므로 최신 목표 TP {targetNinjaTp}만 보존합니다.");
                    return;
                }
            }

            Order anyTpOrder = map.TpOrder;

            Order mappedTpOrder = (anyTpOrder != null &&
                (anyTpOrder.OrderState == OrderState.Working || anyTpOrder.OrderState == OrderState.Accepted))
                ? anyTpOrder : null;

            double tickSize = inst.MasterInstrument.TickSize;
            if (tickSize <= 0.0) tickSize = 0.0000001;

            if (mappedTpOrder != null)
            {
                if (Math.Abs(mappedTpOrder.LimitPrice - targetNinjaTp) < tickSize * 0.5)
                {
                    PrintLog($"ℹ️ [TP 동기화 동일] MT5 Ticket: {masterTicket} | Ninja TP가 이미 {targetNinjaTp}입니다.");
                    return;
                }

                try
                {
                    mappedTpOrder.LimitPriceChanged = targetNinjaTp;
                    targetAccount.Change(new[] { mappedTpOrder });
                    PrintLog($"🔄 [TP 동기화 요청] MT5 Ticket: {masterTicket} | Ninja TP {oldTp} → {targetNinjaTp} | 마스터 TP: {masterTpPrice}");
                }
                catch (Exception ex)
                {
                    map.CurrentTpPrice = oldTp;
                    PrintLog($"⚠️ [TP 동기화 오류] MT5 Ticket: {masterTicket} | {ex.Message}");
                }

                return;
            }

            // 활성 상태가 아니더라도 현재 TP 주문이 Submitted/ChangePending 등 전이 상태면 중복 생성하지 않는다.
            if (anyTpOrder != null &&
                anyTpOrder.OrderState != OrderState.Cancelled &&
                anyTpOrder.OrderState != OrderState.Rejected &&
                anyTpOrder.OrderState != OrderState.Filled)
            {
                PrintLog($"ℹ️ [TP 동기화 대기] MT5 Ticket: {masterTicket} | Ninja TP 주문 상태가 {anyTpOrder.OrderState}이므로 중복 생성하지 않습니다.");
                return;
            }

            // 현재 활성 TP가 없으면 기존 SL 단독 주문까지 함께 교체해 새 OCO 세트를 만든다.
            if (map.CurrentContracts <= 0)
            {
                PrintLog($"⚠️ [TP 동기화 차단] MT5 Ticket: {masterTicket} | 매핑 계약수가 0입니다.");
                map.CurrentTpPrice = oldTp;
                return;
            }

            if (map.CurrentSlPrice <= 0.0)
            {
                PrintLog($"⚠️ [TP 동기화 차단] MT5 Ticket: {masterTicket} | 유효한 Ninja SL 가격이 없어 OCO 세트를 만들 수 없습니다.");
                map.CurrentTpPrice = oldTp;
                return;
            }

            if (!ReplaceProtectionSet(map, inst, map.CurrentSlPrice, targetNinjaTp, "TP 생성 - OCO 세트 전환"))
            {
                // 목표값은 heartbeat 재시도를 위해 유지한다.
                map.CurrentTpPrice = targetNinjaTp;
            }
        }

        // [Order Object Tracking] 같은 Master Ticket에서 파생된 모든 활성 SL/TP 객체를 로컬 레지스트리 기준으로 정리한다.
        private int CancelAllProtectionOrdersForTicket(ulong masterTicket, string reason)
        {
            int count = GetOutstandingProtectionOrders(masterTicket).Count;
            RequestOutstandingProtectionCancels(masterTicket, reason);
            return count;
        }

        // [4단계] Master Ticket 기준 부분청산 / 전체청산
        private void CloseMappedPosition(ulong masterTicket, string fallbackContractSymbol, double targetVol, double masterClosedVol)
        {
            if (targetAccount == null || masterTicket == 0) return;
            RefreshTicketExitQuantities();
            CopyPositionMap map;
            PendingEntryInfo pending;
            lock (ticketMappings) ticketMappings.TryGetValue(masterTicket, out map);
            lock (pendingEntries) pendingEntries.TryGetValue(masterTicket, out pending);
            if (map == null && pending == null)
            {
                PrintLog($"⚠️ [티켓 청산 대상 없음] MT5 Ticket: {masterTicket} | 진입 대기/매핑 없음");
                return;
            }
            if (IsOrphanClosePending(masterTicket)) return;
            bool partial = targetVol > 0.0 && masterClosedVol > 0.0;
            R6Local local = R6Record(masterTicket);
            if (local.SourceInitial <= 0.0) local.SourceInitial = targetVol + masterClosedVol;
            double remainingRatio = partial ? targetVol / local.SourceInitial : 0.0;
            if (double.IsNaN(remainingRatio) || double.IsInfinity(remainingRatio) ||
                remainingRatio < 0.0 || remainingRatio > 1.0 + 1e-9)
                throw new InvalidOperationException("CLOSE_SOURCE_RATIO");
            remainingRatio = Math.Min(1.0, remainingRatio);
            string symbol = map != null ? map.NinjaSymbol : pending.NinjaSymbol;
            QueueTicketClose(masterTicket, symbol, !partial, 1.0 - remainingRatio, remainingRatio);
            PrintLog($"📥 [티켓 청산 접수] MT5 Ticket: {masterTicket} | {(partial ? "부분청산" : "전체청산")} | 진행 중 주문이 있으면 종료 후 최신 목표까지 처리");
        }

        private string MapSymbol(string mt5Symbol, string mnqContract, string mgcContract)
        {
            string s = mt5Symbol.ToUpper();
            if (s.Contains("NAS") || s.Contains("US100") || s.Contains("USTEC")) return mnqContract;
            if (s.Contains("XAU") || s.Contains("GOLD")) return mgcContract;
            return "UNKNOWN";
        }

        private int CalculateSafeContracts(string symbol, double slDist)
        {
            // [TRACE ONLY] 기존 리스크 계산 입력값
            PrintLog($"🔎 [TRACE/RISK INPUT] Symbol={symbol} | slDist={slDist} | MaxRiskText='{settings.Risk.ToString("R", Oz11.Inv)}'");
            if (double.IsNaN(slDist) || double.IsInfinity(slDist) || slDist <= 0) return 0;

            double maxRiskUsd = settings.Risk;
            if (!Oz11.Finite(maxRiskUsd) || maxRiskUsd <= 0) return 0;

            double riskPerContract = 0;
            if (symbol.Contains("MNQ")) riskPerContract = slDist * 2.0;       
            else if (symbol.Contains("MGC")) riskPerContract = slDist * 10.0; 

            // [TRACE ONLY] 기존 리스크 차단 판정 직전 계산값
            PrintLog($"🔎 [TRACE/RISK CALC] Symbol={symbol} | slDist={slDist} | riskPerContract={riskPerContract} | maxRiskUsd={maxRiskUsd}");
            if (riskPerContract > maxRiskUsd || riskPerContract <= 0) return 0;
            double contracts = Math.Floor(maxRiskUsd / riskPerContract);
            if (double.IsNaN(contracts) || double.IsInfinity(contracts) || contracts < 1 || contracts > int.MaxValue) return 0;
            return (int)contracts;
        }


        // R11: this class has no WPF controls. Every account owns its own serial executor and signal inbox.
        private readonly string v2StreamId;
        private readonly string accountIdentity,registeredConnection;
        private volatile OzAccountSettings settings;
        private OzEnableSelection enableSelection;
        private int settingsEditing,settingsPending,settingsRevision;
        private ulong statusPublishTick;
        private readonly OzRiskManager risk;
        private readonly OzSerialExecutor Dispatcher;
        private readonly object signalGate=new object();
        private readonly Queue<OzMasterReader.Envelope> incoming=new Queue<OzMasterReader.Envelope>();
        private OzMasterReader.Pulse observation;
        private long observedEpoch=-1;
        private volatile bool resetRequested=true;
        private string externalFault="";
        private OzCommState comm=OzCommState.TEMP_DISCONNECTED;
        private string commReason="STARTING", boundSession="";
        private ulong boundLogin,accepted,lastTick,maintenanceTick;
        private ulong adoptVersion=0;private string adoptSession="";
        private string boundServer="";
        private bool needsRebase=true,manualRecovery;
        private volatile bool disposed;
        private OzControl11.Request checkRequest;
        private ulong lastRequest;
        private string lastRequestSession="";
        private ulong requestAt;
        private bool checkFailureLogged;
        private volatile OzAccountStatus publishedStatus;
        private readonly Dictionary<string,List<Tuple<ulong,int,string>>> orphanAllocations=new Dictionary<string,List<Tuple<ulong,int,string>>>();
        internal OzAccountStatus Status { get { return publishedStatus; } }
        internal OzAccountSettings Settings { get { var copy=settings.Copy();copy.Enabled=RequestedEnabled;return copy; } }
        internal OzEnableSelection EnableSnapshot { get { return Volatile.Read(ref enableSelection); } }
        internal bool RequestedEnabled { get { return !disposed&&EnableSnapshot.Enabled; } }
        internal ulong EnableRevision { get { return EnableSnapshot.Revision; } }
        private bool EntrySwitchOpen { get { return RequestedEnabled&&Volatile.Read(ref settingsEditing)==0&&Volatile.Read(ref settingsPending)==0; } }
        internal string AccountKey { get { return accountIdentity; } }
        internal OzAccountEngine(Account account,OzAccountSettings configuration,string stream)
        {
            targetAccount=account;settings=configuration.Copy();settings.Validate();v2StreamId=stream;
            enableSelection=new OzEnableSelection(settings.Enabled,OzMasterReader.Tick(),1);
            registeredConnection=Oz11.ConnectionName(account);accountIdentity=account.Name+"@"+registeredConnection;
            if(account.Denomination!=Currency.UsDollar)throw new InvalidOperationException("USD_ACCOUNT_REQUIRED_FOR_RISK_DOLLARS");
            EventWaitHandle lease;
            if(!R8TryAccountLease(account.Name,out lease))throw new InvalidOperationException("ACCOUNT_ALREADY_OWNED");
            r8AccountLease=lease;r8LeasedAccount=account.Name;
            try{risk=new OzRiskManager(accountIdentity+"|"+stream,settings,PrintLog);}
            catch{R8ReleaseAccountLease();throw;}
            Dispatcher=new OzSerialExecutor(TickEngine,ex=>Interlocked.Exchange(ref externalFault,"EXECUTOR_EXCEPTION:"+ex.GetType().Name));
            publishedStatus=new OzAccountStatus{Account=account.Name,Connection=Oz11.ConnectionName(account),RegistryConnection=registeredConnection,Enabled=settings.Enabled};
            targetAccount.OrderUpdate+=OnOrderUpdate;targetAccount.ExecutionUpdate+=OnExecutionUpdate;
            Dispatcher.Start("OZ account "+account.Name);
        }
        internal void Feed(OzMasterReader.Pulse pulse)
        {
            lock(signalGate)
            {
                observation=pulse;
                if(pulse.Epoch!=observedEpoch||pulse.Link!=OzCommState.RUNNING)
                { observedEpoch=pulse.Epoch;resetRequested=true; }
                foreach(var e in pulse.Commands)
                {
                    if(incoming.Count>=4096){Interlocked.Exchange(ref externalFault,"ACCOUNT_SIGNAL_QUEUE_FULL");resetRequested=true;break;}
                    incoming.Enqueue(e);
                }
            }
            if(pulse.Commands.Count>0)Dispatcher.Wake();
        }
        internal void DispatchFault(string why) { Interlocked.Exchange(ref externalFault,why); }
        internal void RequestCheck(OzControl11.Request request)
        { Interlocked.Exchange(ref checkRequest,request); }
        internal void SetEnabled(bool value)
        {
            while(!disposed||!value)
            {
                var old=EnableSnapshot;if(old.Enabled==value)return;
                var next=new OzEnableSelection(value,OzMasterReader.Tick(),old.Revision+1);
                if(ReferenceEquals(Interlocked.CompareExchange(ref enableSelection,next,old),old))return;
            }
        }
        internal void BeginSettingsEdit(){Interlocked.Exchange(ref settingsEditing,1);}
        internal void CancelSettingsEdit(){Interlocked.Exchange(ref settingsEditing,0);}
        internal void Apply(OzAccountSettings next,Action<string> complete)
        {
            next=next.Copy();next.Validate();int revision=Interlocked.Increment(ref settingsRevision);
            Interlocked.Exchange(ref settingsPending,revision);
            bool posted=Dispatcher.TryPost(()=>
            {
                string error="";
                try
                {
                    if((next.Mnq!=settings.Mnq||next.Mgc!=settings.Mgc)&&!V2AccountFlat())
                        throw new InvalidOperationException("포지션·주문 관리 중 월물 변경은 허용하지 않습니다.");
                    next.Enabled=RequestedEnabled;risk.UpdateSettings(next);settings=next;SaveSettings(next);
                }
                catch(Exception ex){error=ex.Message;PrintLog("[SETTINGS_REJECTED] "+error);}
                Interlocked.CompareExchange(ref settingsPending,0,revision);
                if(complete!=null)complete(error);
            });
            if(!posted)
            {
                SetEnabled(false);Interlocked.CompareExchange(ref settingsPending,0,revision);CancelSettingsEdit();
                if(complete!=null)complete("계좌 실행기가 설정을 받지 못했습니다. 사용을 해제했습니다.");
            }
        }
        private void SaveSettings(OzAccountSettings snapshot)
        {
            snapshot=snapshot.Copy();
            if(!OzSettingsWriter12.Post(()=>
            {
            try
            {
                var r=new System.Xml.Linq.XElement("account",new System.Xml.Linq.XAttribute("risk",snapshot.Risk.ToString("R",Oz11.Inv)),
                    new System.Xml.Linq.XAttribute("daily",snapshot.DailyLossPercent.ToString("R",Oz11.Inv)),new System.Xml.Linq.XAttribute("max",snapshot.MaxPositions),
                    new System.Xml.Linq.XAttribute("losses",snapshot.DailyLossTrades),new System.Xml.Linq.XAttribute("consecutive",snapshot.ConsecutiveLosses),
                    new System.Xml.Linq.XAttribute("cooldown",snapshot.CooldownMinutes),new System.Xml.Linq.XAttribute("zone",snapshot.DayZone),
                    new System.Xml.Linq.XAttribute("start",snapshot.DayStartMinutes));
                Oz11.SaveXml(Oz11.PathFor("settings",accountIdentity+"|"+v2StreamId),new System.Xml.Linq.XDocument(r));
            }
            catch{SetEnabled(false);DispatchFault("SETTINGS_SAVE_FAILED");}
            })) {SetEnabled(false);DispatchFault("SETTINGS_SAVE_QUEUE_FULL");}
        }
        internal static OzAccountSettings LoadSettings(string key,string stream,OzAccountSettings defaults)
        {
            var s=defaults.Copy();string file=Oz11.PathFor("settings",key+"|"+stream);if(!System.IO.File.Exists(file))return s;
            var r=System.Xml.Linq.XDocument.Load(file).Root;
            s.Risk=double.Parse((string)r.Attribute("risk"),Oz11.Inv);s.DailyLossPercent=double.Parse((string)r.Attribute("daily"),Oz11.Inv);
            s.MaxPositions=int.Parse((string)r.Attribute("max"),Oz11.Inv);s.DailyLossTrades=int.Parse((string)r.Attribute("losses"),Oz11.Inv);
            s.ConsecutiveLosses=int.Parse((string)r.Attribute("consecutive"),Oz11.Inv);s.CooldownMinutes=int.Parse((string)r.Attribute("cooldown"),Oz11.Inv);
            s.DayZone=(string)r.Attribute("zone");s.DayStartMinutes=int.Parse((string)r.Attribute("start"),Oz11.Inv);s.Validate();return s;
        }
        private void SetComm(OzCommState state,string why)
        {
            if(comm!=state)
                Oz11.Burst(state==OzCommState.HARD_HOLD?"MMF RECOVERY REQUIRED":"MMF TEMP DISCONNECTED",why,accountIdentity);
            comm=state;commReason=why;needsRebase=true;
        }
        private bool TargetSame()
        { return targetAccount!=null&&targetAccount.Name+"@"+Oz11.ConnectionName(targetAccount)==accountIdentity; }
        private int OpenSlots()
        {
            lock(ticketMappings)lock(pendingEntries)
                return ticketMappings.Values.Where(m=>m.CurrentContracts>0).Select(m=>m.MasterTicket).Concat(pendingEntries.Keys).Distinct().Count();
        }
        private bool EntryAllowed(out string reason)
        {
            reason="READY";
            if(!TargetSame()){reason="TARGET_ACCOUNT_CHANGED";return false;}
            if(!Oz11.BrokerConnected(targetAccount)){reason="BROKER_DISCONNECTED";return false;}
            if(!RequestedEnabled){reason="DISABLED";return false;}
            if(Volatile.Read(ref settingsEditing)!=0||Volatile.Read(ref settingsPending)!=0){reason="SETTINGS_PENDING";return false;}
            if(comm!=OzCommState.RUNNING||needsRebase){reason=needsRebase?"RECOVERY_CHECKING":commReason;return false;}
            lock(ticketMappings)
                if(ticketMappings.Values.Any(m=>m.QuantityFault||(m.CurrentContracts==0&&!risk.CanFinalize(m.MasterTicket,m.InitialContracts))))
                {reason="EXECUTION_ACCOUNTING_PENDING";return false;}
            return risk.EntryAllowed(OpenSlots(),DateTime.UtcNow,out reason);
        }
        private bool ValidActualOwnership()
        {
            RefreshTicketExitQuantities();List<CopyPositionMap> maps;lock(ticketMappings)maps=ticketMappings.Values.ToList();
            if(maps.Any(m=>m.QuantityFault))return false;
            List<Position> actual;lock(targetAccount.Positions)actual=targetAccount.Positions.Where(p=>p.Quantity>0&&p.MarketPosition!=MarketPosition.Flat).ToList();
            foreach(var g in maps.Where(m=>m.CurrentContracts>0).GroupBy(m=>m.NinjaSymbol))
            {
                if(g.Select(m=>m.Direction).Distinct().Count()!=1)return false;
                Position p=actual.FirstOrDefault(v=>v.Instrument.FullName==g.Key);
                if(p==null||p.Quantity!=g.Sum(m=>m.CurrentContracts)||p.MarketPosition!=(g.First().Direction=="BUY"?MarketPosition.Long:MarketPosition.Short))return false;
            }
            if(actual.Any(p=>!maps.Any(m=>m.CurrentContracts>0&&m.NinjaSymbol==p.Instrument.FullName)))return false;
            List<Order> orders;lock(targetAccount.Orders)orders=targetAccount.Orders.ToList();
            foreach(Order o in orders)
            { if(IsProtectionOrderTerminal(o.OrderState))continue;TrackedOrderInfo t;if(!TryGetTrackedOrderInfo(o,out t))return false; }
            return true;
        }
        private bool Recover(OzMasterReader.Snapshot s)
        {
            if(s==null||s.Full==null||!s.Full.Consistent||s.Base!=s.Published||!Oz11.Fresh(OzMasterReader.Tick(),s.Alive)||!Oz11.Fresh(OzMasterReader.Tick(),s.StateTick))return false;
            if(!TargetSame()){SetComm(OzCommState.HARD_HOLD,"TARGET_ACCOUNT_CHANGED");return false;}
            if(boundLogin!=0&&(boundLogin!=s.Login||boundServer!=s.Server)){SetComm(OzCommState.HARD_HOLD,"SOURCE_ACCOUNT_CHANGED");return false;}
            if(!Oz11.BrokerConnected(targetAccount))return false;
            // Do not erase submitted entry state; cancel residual entry and await terminal observations.
            List<ulong> entries;lock(pendingEntries)entries=pendingEntries.Keys.ToList();
            foreach(ulong id in entries)RequestEntryCancel(id);
            if(entries.Count>0)return false;
            if(!ValidActualOwnership()){SetComm(OzCommState.HARD_HOLD,"LOCAL_OWNERSHIP_OR_QUANTITY_MISMATCH");return false;}
            if(risk.DataFault){SetComm(OzCommState.HARD_HOLD,risk.Reason);return false;}
            List<CopyPositionMap> maps;lock(ticketMappings)maps=ticketMappings.Values.ToList();
            foreach(var map in maps)
            {
                var row=s.Full.Rows.FirstOrDefault(r=>r.Id==map.MasterTicket);
                if(row!=null&&(row.Side!=map.Direction||MapSymbol(row.Symbol,settings.Mnq,settings.Mgc)!=map.NinjaSymbol||map.MasterEntryPrice<=0||map.InitialContracts<=0))
                {SetComm(OzCommState.HARD_HOLD,"LOCAL_IDENTITY_MISMATCH");return false;}
            }
            foreach(var row in s.Full.Rows)
            {
                var local=R6Record(row.Id);local.Adopted=true;local.EntrySequence=row.EntrySequence;local.LastSequence=row.LastSequence;
                local.Symbol=row.Symbol;local.Side=row.Side;local.SourceInitial=row.InitialVolume;local.SourceRemaining=row.Volume;
                var map=maps.FirstOrDefault(m=>m.MasterTicket==row.Id);
                if(map==null||map.CurrentContracts==0){local.NoOpen=true;continue;} // explicitly skipped, never late BUY
                local.NoOpen=false;local.CloseSeen=true;
                double ratio=row.Volume/row.InitialVolume;
                int target=ratio==0?0:Math.Max(1,(int)Math.Ceiling(map.InitialContracts*ratio-1e-9));
                local.ExpectedContracts=Math.Min(map.CurrentContracts,target);
                if(map.CurrentContracts<target)local.LocalExit=true;
                if(map.CurrentContracts>target)QueueTicketClose(map.MasterTicket,map.NinjaSymbol,false,1-ratio,ratio);
            }
            foreach(var map in maps.Where(m=>m.CurrentContracts>0&&!s.Full.Rows.Any(r=>r.Id==m.MasterTicket)))
                QueueTicketClose(map.MasterTicket,map.NinjaSymbol,true,1.0);
            // Atomic inbox cutover. Order objects, mappings, PnL and risk locks survive this operation.
            lock(signalGate)
            {
                boundLogin=s.Login;boundServer=s.Server;boundSession=s.Session;accepted=s.Published;
                var keep=incoming.Where(e=>e.Session==s.Session&&e.Sequence>s.Published).ToArray();incoming.Clear();foreach(var e in keep)incoming.Enqueue(e);
            }
            if(!V2TransitionsDone())return false;
            if(!R6Reconcile(s))return false;
            ProcessHeartbeatSync(s.Payload.Substring("ACTION=SYNC|TICKETS=".Length));
            if(!V2TransitionsDone())return false;
            needsRebase=false;comm=OzCommState.RUNNING;commReason="CONNECTED";
            Oz11.Burst(manualRecovery?"MMF FORCE RECOVERED":"MMF AUTO RECOVERED","SAME MASTER CONNECTED",accountIdentity);
            manualRecovery=false;checkFailureLogged=false;return true;
        }
        private void TickEngine()
        {
            if(disposed)return;
            ulong now=OzMasterReader.Tick();string fault=Interlocked.Exchange(ref externalFault,"");
            if(fault!="") { if(!risk.DataFault)risk.Reason=fault;risk.DataFault=true;SetComm(OzCommState.HARD_HOLD,fault); }
            if(lastTick>0&&!Oz11.Fresh(now,lastTick))SetComm(OzCommState.TEMP_DISCONNECTED,"EXECUTOR_SCHEDULING_GAP");
            lastTick=now;
            OzMasterReader.Pulse p;lock(signalGate){p=observation;if(resetRequested){needsRebase=true;resetRequested=false;}}
            if(!TargetSame()){SetComm(OzCommState.HARD_HOLD,"TARGET_ACCOUNT_CHANGED");PublishStatus();return;}
            bool broker=Oz11.BrokerConnected(targetAccount);
            if(now-maintenanceTick>=250)
            {
                maintenanceTick=now;
                // Account-wide NLV includes manual/other-strategy PnL. Unsupported/zero values block entry.
                double equity=broker?targetAccount.Get(AccountItem.NetLiquidation,Currency.UsDollar):double.NaN;
                risk.ObserveEquity(equity,broker,!V2AccountFlat(),DateTime.UtcNow);
                if(broker)
                {
                    if(risk.DailyLocked)
                    {
                        List<ulong> ids;lock(pendingEntries)ids=pendingEntries.Keys.ToList();foreach(ulong id in ids)RequestEntryCancel(id);
                        List<CopyPositionMap> maps;lock(ticketMappings)maps=ticketMappings.Values.ToList();
                        foreach(var m in maps.Where(m=>m.CurrentContracts>0))
                            if(!IsFullClosePending(m.MasterTicket)&&!IsOrphanClosePending(m.MasterTicket))QueueTicketClose(m.MasterTicket,m.NinjaSymbol,true,1.0);
                    }
                    RefreshMissedCallbacks();
                    CheckPendingProtectionReplacements();CheckPendingFullCloseRequests();CheckPendingOrphanCloseRequests();CleanupExpiredTrackedOrders();
                }
            }
            if(!broker)SetComm(OzCommState.TEMP_DISCONNECTED,"BROKER_DISCONNECTED");
            else if(p==null||p.State==null)SetComm(OzCommState.TEMP_DISCONNECTED,"WAITING_FOR_MASTER");
            else if(p.Link!=OzCommState.RUNNING)SetComm(p.Link,p.Reason);
            else if(!Oz11.Fresh(now,p.State.Alive))SetComm(OzCommState.TEMP_DISCONNECTED,"HEARTBEAT_EXPIRED");
            else
            {
                if(boundSession!=p.State.Session)needsRebase=true;
                if(needsRebase)Recover(p.State);
                if(!needsRebase&&comm==OzCommState.RUNNING)
                {
                    OzMasterReader.Envelope item=null;
                    lock(signalGate)
                    {
                        while(incoming.Count>0&&(incoming.Peek().Session!=boundSession||incoming.Peek().Sequence<=accepted))incoming.Dequeue();
                        if(incoming.Count>0)item=incoming.Peek();
                    }
                    if(item!=null)
                    {
                        if(item.Sequence!=accepted+1||!Oz11.Fresh(now,item.PublishedTick))SetComm(OzCommState.TEMP_DISCONNECTED,"COMMAND_GAP_OR_STALE");
                        else
                        {
                            ProcessRawMessage(item);accepted=item.Sequence;
                            lock(signalGate)if(incoming.Count>0&&ReferenceEquals(incoming.Peek(),item))incoming.Dequeue();
                        }
                    }
                    else if(p.State.Base==accepted&&p.State.Published==accepted&&p.State.Version!=r6Reported&&V2TransitionsDone())
                    {
                        if(R6Reconcile(p.State))ProcessHeartbeatSync(p.State.Payload.Substring("ACTION=SYNC|TICKETS=".Length));
                        else if(p.State.Full!=null&&p.State.Full.Consistent)SetComm(OzCommState.HARD_HOLD,"SNAPSHOT_ACCOUNT_MISMATCH");
                    }
                }
            }
            OzControl11.Request q=Interlocked.CompareExchange(ref checkRequest,null,null);
            if(RequestedEnabled&&q!=null&&(q.Id!=lastRequest||q.Session!=lastRequestSession))
            {
                lastRequest=q.Id;lastRequestSession=q.Session;requestAt=now;checkFailureLogged=false;
                if(p==null||p.State==null||q.Source!=p.State.Login||q.Server!=p.State.Server)
                    SetComm(OzCommState.HARD_HOLD,"RECOVERY_SOURCE_MISMATCH");
                else if(q.MasterReady&&comm==OzCommState.RUNNING&&!needsRebase&&broker&&Oz11.Fresh(now,p.State.Alive)&&boundSession==p.State.Session)
                    Oz11.Burst("MMF CONNECTION CHECK","ALREADY CONNECTED",accountIdentity);
                else{manualRecovery=true;needsRebase=true;}
            }
            if(manualRecovery&&now-requestAt>5000&&!checkFailureLogged)
            {Oz11.Burst("MMF RECOVERY FAILED",commReason,accountIdentity);checkFailureLogged=true;}
            PublishStatus();
        }
        private void PublishStatus()
        {
            ulong publishNow=OzMasterReader.Tick();
            if(statusPublishTick!=0&&publishNow-statusPublishTick<250)return;
            statusPublishTick=publishNow;
            OzMasterReader.Pulse pulse;lock(signalGate)pulse=observation;
            bool live=comm==OzCommState.RUNNING&&!needsRebase&&pulse!=null&&pulse.Link==OzCommState.RUNNING&&pulse.State!=null&&
                pulse.State.Session==boundSession&&Oz11.Fresh(OzMasterReader.Tick(),pulse.State.Alive)&&Oz11.BrokerConnected(targetAccount);
            string entryReason;bool entry=EntryAllowed(out entryReason);
            if(entry&&!live){entry=false;entryReason="RECOVERY_CHECKING";}
            if(entry&&(pulse.State.Full==null||!pulse.State.Full.Consistent||pulse.State.Base!=pulse.State.Published||accepted!=pulse.State.Published))
            {entry=false;entryReason="SOURCE_SYNC_PENDING";}
            bool queued;lock(signalGate)queued=incoming.Count>0;
            if(entry&&(queued||!V2TransitionsDone())){entry=false;entryReason="ACCOUNT_PROCESSING";}
            publishedStatus=new OzAccountStatus{Account=targetAccount.Name,Connection=Oz11.ConnectionName(targetAccount),RegistryConnection=registeredConnection,Enabled=RequestedEnabled,BrokerOnline=Oz11.BrokerConnected(targetAccount),
                State=live?(risk.DailyLocked?"DAILY_LOCK":entryReason=="COOLDOWN"?"COOLDOWN":"RUNNING"):(comm==OzCommState.HARD_HOLD?"HARD_HOLD":"TEMP_DISCONNECTED"),
                Reason=live?entryReason:(needsRebase&&comm==OzCommState.RUNNING?"RECOVERY_CHECKING":commReason),LinkReady=live,EntryReady=entry,CanStop=V2AccountFlat(),
                Tick=OzMasterReader.Tick(),Accepted=accepted,Session=boundSession,SourceLogin=boundLogin,Request=lastRequest,RequestSession=lastRequestSession,
                Pnl=risk.Pnl,Equity=risk.Equity,Positions=OpenSlots(),DailyLosses=risk.DailyLosses,ConsecutiveLosses=risk.Consecutive,CooldownLeft=risk.CooldownLeft};
        }
        private void ObserveRiskExecution(Order order,string key,int quantity,double price,double commission,double pointValue,DateTime fillUtc)
        {
            TrackedOrderInfo t;
            if(!TryGetTrackedOrderInfo(order,out t))
            {
                List<Tuple<ulong,int,string>> priorAllocation;
                if(orphanAllocations.TryGetValue(key,out priorAllocation))
                {
                    if(quantity<=0||priorAllocation.Sum(a=>a.Item2)!=quantity){risk.DataFault=true;risk.Reason="ORPHAN_EXECUTION_CORRECTION";return;}
                    foreach(var a in priorAllocation)risk.OnFill(a.Item1,a.Item3,key+":"+a.Item1,false,a.Item2,price,commission*a.Item2/quantity,pointValue,fillUtc);
                }
                else risk.UpdateKnownExecution(key,quantity,price,commission,pointValue,fillUtc);
                risk.Save(false);return;
            }
            string side;
            CopyPositionMap map;lock(ticketMappings)ticketMappings.TryGetValue(t.MasterTicket,out map);
            side=map!=null?map.Direction:t.EntryContext!=null?t.EntryContext.Direction:null;
            if(side==null&&risk.Tickets.ContainsKey(t.MasterTicket))side=risk.Tickets[t.MasterTicket].Side;
            if(side==null){risk.DataFault=true;risk.Reason="EXECUTION_WITHOUT_TICKET_SIDE";return;}
            if(t.Role==TrackedOrderRole.OrphanClose)
            {
                List<Tuple<ulong,int,string>> existing;
                if(orphanAllocations.TryGetValue(key,out existing))
                {
                    if(existing.Sum(a=>a.Item2)!=quantity){risk.DataFault=true;risk.Reason="ORPHAN_EXECUTION_CORRECTION";return;}
                    foreach(var a in existing)risk.OnFill(a.Item1,a.Item3,key+":"+a.Item1,false,a.Item2,price,commission*a.Item2/quantity,pointValue,fillUtc);
                    return;
                }
                var allocation=new List<Tuple<ulong,int,string>>();
                string symbol=order.Instrument.FullName;PendingOrphanCloseRequest request;
                lock(orphanCloseLock)pendingOrphanCloseRequests.TryGetValue(symbol,out request);
                if(request==null){risk.DataFault=true;risk.Reason="ORPHAN_EXECUTION_WITHOUT_OWNER";return;}
                int remaining=quantity;
                foreach(ulong id in request.StaleMasterTickets.OrderBy(v=>v))
                {
                    OzRiskManager.Ticket ledger;if(!risk.Tickets.TryGetValue(id,out ledger))continue;
                    int used=Math.Min(remaining,Math.Max(0,ledger.Entered-ledger.Exited));if(used==0)continue;
                    risk.OnFill(id,ledger.Side,key+":"+id,false,used,price,commission*used/quantity,pointValue,fillUtc);allocation.Add(Tuple.Create(id,used,ledger.Side));remaining-=used;
                }
                if(remaining!=0){risk.DataFault=true;risk.Reason="ORPHAN_FILL_ALLOCATION_MISMATCH";return;}
                orphanAllocations[key]=allocation;
            }
            else risk.OnFill(t.MasterTicket,side,key,t.Role==TrackedOrderRole.Entry,quantity,price,commission,pointValue,fillUtc);
            risk.Save(false);
        }
        private void OnExecutionUpdate(object sender,ExecutionEventArgs e)
        {
            if(e==null||e.Execution==null)return;
            var x=e.Execution;Order order=x.Order;
            string key=x.ExecutionId;int qty=x.Quantity;double price=x.Price,commission=x.Commission;
            double point=x.Instrument==null?0:x.Instrument.MasterInstrument.PointValue;
            DateTime fillUtc;try{fillUtc=Oz11.ExecutionUtc(x.Time);}catch(Exception ex){DispatchFault(ex.Message);return;}
            int filled=order==null?0:order.Filled;double average=order==null?0:order.AverageFillPrice;OrderState state=order==null?OrderState.Cancelled:order.OrderState;
            Dispatcher.InvokeAsync(()=>{ObserveRiskExecution(order,key,qty,price,commission,point,fillUtc);if(order!=null)ProcessOrderObservation(order,state,filled,average);});
        }
        private void PrintLog(string message){Oz11.Log("["+accountIdentity+"] "+message);}
        internal void Dispose()
        {
            if(disposed)return;disposed=true;SetEnabled(false);
            targetAccount.OrderUpdate-=OnOrderUpdate;targetAccount.ExecutionUpdate-=OnExecutionUpdate;
            Dispatcher.Dispose();R8ReleaseAccountLease();
        }

        private void RefreshMissedCallbacks()
        {
            // Account-provided executions repair delayed callbacks; never invent fills from net position changes.
            List<Execution> executions;lock(targetAccount.Executions)executions=targetAccount.Executions.ToList();
            int scanned=0;
            for(int i=executions.Count-1;i>=0&&scanned<2048;i--,scanned++)
            {
                var x=executions[i];if(x==null||x.Order==null||x.Instrument==null)continue;
                TrackedOrderInfo t;if(!TryGetTrackedOrderInfo(x.Order,out t))continue;
                ObserveRiskExecution(x.Order,x.ExecutionId,x.Quantity,x.Price,x.Commission,x.Instrument.MasterInstrument.PointValue,Oz11.ExecutionUtc(x.Time));
            }
            List<Order> tracked;lock(trackedOrdersLock)tracked=trackedOrders.Keys.ToList();
            foreach(var order in tracked)
            {
                TrackedOrderInfo t;if(!TryGetTrackedOrderInfo(order,out t))continue;
                if(order.Filled>t.ObservedFilled||(IsProtectionOrderTerminal(order.OrderState)&&!t.TerminalHandled))
                    ProcessOrderObservation(order,order.OrderState,order.Filled,order.AverageFillPrice);
            }
        }

        private List<Position> PositionSnapshot() { lock(targetAccount.Positions)return targetAccount.Positions.ToList(); }
        private List<Order> OrderSnapshot() { lock(targetAccount.Orders)return targetAccount.Orders.ToList(); }
        internal void StopIfFlat(Action<bool> done)
        {
            Dispatcher.InvokeAsync(()=>
            {
                SetEnabled(false);
                if(!V2AccountFlat()){done(false);return;}
                risk.Save(true);Dispose();done(true);
            });
        }

    }
    // R12: trade fan-out and ACK/membership use separate threads. UI publishes immutable lists.
    internal sealed class OzReceiverHub : IDisposable
    {
        private readonly OzMasterReader reader;
        private readonly OzControl11 control;
        private readonly object gate=new object();
        private volatile OzAccountEngine[] accounts=new OzAccountEngine[0];
        private readonly System.Collections.Concurrent.ConcurrentQueue<string> removed=new System.Collections.Concurrent.ConcurrentQueue<string>();
        private readonly CancellationTokenSource cancel=new CancellationTokenSource();
        private readonly Thread thread,controlThread;
        private EventWaitHandle streamLease;
        private volatile OzMasterReader.Pulse current;
        private ulong lastRequest;
        private string requestSession="";
        private int workers=2;
        internal OzMasterReader.Pulse Current { get { return current; } }
        internal OzReceiverHub(string stream,ulong login,string server)
        {
            bool created;
            streamLease=new EventWaitHandle(false,EventResetMode.ManualReset,@"Local\OZCopy.Multi12."+Oz11.Hash(stream),out created);
            if(!created){streamLease.Dispose();streamLease=null;throw new InvalidOperationException("같은 스트림의 다계좌 수신기가 이미 실행 중입니다.");}
            try {reader=new OzMasterReader(stream,login,server);control=new OzControl11(stream);}
            catch {streamLease.Dispose();streamLease=null;throw;}
            thread=new Thread(Run){IsBackground=true,Name="OZ shared MMF reader"};
            controlThread=new Thread(RunControl){IsBackground=true,Name="OZ status ACK",Priority=ThreadPriority.BelowNormal};
            bool readerStarted=false;
            try{thread.Start();readerStarted=true;controlThread.Start();}
            catch
            {
                cancel.Cancel();
                if(!readerStarted){reader.Dispose();WorkerStopped();}
                control.Dispose();WorkerStopped();throw;
            }
        }
        internal void Add(OzAccountEngine engine)
        {
            lock(gate)
            {
                var list=accounts;
                if(list.Length>=20)throw new InvalidOperationException("MAX_20_ACCOUNTS");
                if(list.Any(a=>a.AccountKey==engine.AccountKey))throw new InvalidOperationException("DUPLICATE_ACCOUNT");
                accounts=list.Concat(new[]{engine}).ToArray();
            }
        }
        internal void Remove(OzAccountEngine engine)
        {
            engine.SetEnabled(false);
            lock(gate)accounts=accounts.Where(a=>!ReferenceEquals(a,engine)).ToArray();
            removed.Enqueue(engine.AccountKey);
        }
        private void Run()
        {
            try
            {
                while(!cancel.IsCancellationRequested)
                {
                    var p=reader.Poll();current=p;
                    var list=accounts;
                    foreach(var a in list)
                    {try{a.Feed(p);}catch(Exception ex){a.DispatchFault("ACCOUNT_DISPATCH_EXCEPTION:"+ex.GetType().Name);}}
                    // No ACK, UI, roster counting or disk write in this loop.
                    cancel.Token.WaitHandle.WaitOne(10); // existing idle poll cadence, not a new monitoring wait
                }
            }
            finally{reader.Dispose();WorkerStopped();}
        }
        private void RunControl()
        {
            try
            {
                while(!cancel.IsCancellationRequested)
                {
                    var list=accounts;
                    try
                    {
                        var q=control.ReadRequest();
                        if(q!=null&&q.Id>0&&(q.Id!=lastRequest||q.Session!=requestSession))
                        {
                            lastRequest=q.Id;requestSession=q.Session;
                            if(current==null||current.Link!=OzCommState.RUNNING)reader.RequestProbe();
                        }
                        foreach(var a in list)
                        {
                            // OFF accounts still publish membership but receive no manual Recovery request.
                            if(q!=null&&q.Id>0&&a.RequestedEnabled)a.RequestCheck(q);
                            try{var selection=a.EnableSnapshot;control.WriteStatus(a.Status,selection.Enabled,selection.Revision);}
                            catch(Exception ex){Oz11.Log("[RECOVERY_ACK_ERROR] "+a.AccountKey+" | "+ex.GetType().Name);}
                        }
                        for(int i=0;i<64;i++)
                        {
                            string key;if(!removed.TryDequeue(out key))break;
                            if(list.Any(a=>a.AccountKey==key))continue;
                            if(!control.RemoveAccount(key)){removed.Enqueue(key);break;}
                        }
                    }
                    catch(Exception ex){control.Dispose();Oz11.Log("[RECOVERY_CHANNEL_ERROR] "+ex.GetType().Name);}
                    cancel.Token.WaitHandle.WaitOne(250);
                }
            }
            finally {control.ClearOwned();control.Dispose();WorkerStopped();}
        }
        private void WorkerStopped()
        {
            if(Interlocked.Decrement(ref workers)!=0)return;
            var lease=Interlocked.Exchange(ref streamLease,null);if(lease!=null)lease.Dispose();
            cancel.Dispose();
        }
        private int stopping;
        public void Dispose(){if(Interlocked.Exchange(ref stopping,1)==0)cancel.Cancel();}
    }

    internal sealed class OzAccountRow : System.ComponentModel.INotifyPropertyChanged
    {
        internal OzAccountEngine Engine;
        internal Action SelectionChanged;
        private bool enabled;
        internal bool Editing,Applying;
        internal int ApplyRevision;
        internal OzAccountSettings PendingSettings;
        internal string UiError="";
        public bool Enable
        {
            get{return enabled;}
            set
            {
                if(enabled==value)return;
                if(Engine!=null)Engine.SetEnabled(value);
                enabled=value;Changed("Enable");if(SelectionChanged!=null)SelectionChanged();
            }
        }
        public string Account { get;set; }
        public string Connection { get;set; }
        public double Risk { get;set; }
        public double DailyMaxLoss { get;set; }
        public int MaxPositions { get;set; }
        public int LossLimit { get;set; }
        public int ConsecutiveLimit { get;set; }
        public int CooldownMinutes { get;set; }
        public string DayZone { get;set; }
        public int DayStartMinutes { get;set; }
        public string TodayPnL { get;set; }
        public int OpenPositions { get;set; }
        public int DailyLosses { get;set; }
        public int ConsecutiveLosses { get;set; }
        public string CooldownLeft { get;set; }
        public string State { get;set; }
        public string Reason { get;set; }
        public string Detail { get;set; }
        public event System.ComponentModel.PropertyChangedEventHandler PropertyChanged;
        private void Changed(string name){var h=PropertyChanged;if(h!=null)h(this,new System.ComponentModel.PropertyChangedEventArgs(name));}
        internal void LoadValues(OzAccountSettings c)
        {
            Risk=c.Risk;DailyMaxLoss=c.DailyLossPercent;MaxPositions=c.MaxPositions;LossLimit=c.DailyLossTrades;
            ConsecutiveLimit=c.ConsecutiveLosses;CooldownMinutes=c.CooldownMinutes;DayZone=c.DayZone;DayStartMinutes=c.DayStartMinutes;
            foreach(string n in new[]{"Risk","DailyMaxLoss","MaxPositions","LossLimit","ConsecutiveLimit","CooldownMinutes"})Changed(n);
        }
        internal void Refresh()
        {
            var s=Engine.Status;if(s==null)return;
            bool fresh=Oz11.Fresh(OzMasterReader.Tick(),s.Tick);
            bool actual=Engine.RequestedEnabled;
            if(enabled!=actual){enabled=actual;Changed("Enable");if(SelectionChanged!=null)SelectionChanged();}
            TodayPnL=Oz11.Finite(s.Equity)?s.Pnl.ToString("F2",Oz11.Inv):"—";OpenPositions=s.Positions;DailyLosses=s.DailyLosses;ConsecutiveLosses=s.ConsecutiveLosses;
            CooldownLeft=TimeSpan.FromSeconds(s.CooldownLeft).ToString(@"hh\:mm\:ss");
            Connection=fresh&&s.BrokerOnline?"연결됨":"연결 끊김";
            Reason=UiError!=""?UiError:fresh?s.Reason:"EXECUTOR_NO_RESPONSE";
            State=UiError!=""?"설정 확인":Editing||Applying?"설정 중":!fresh?"DISCONNECTED":!Enable?"사용 안 함":s.EntryReady?"READY":
                s.State=="HARD_HOLD"?"HOLD":s.State=="TEMP_DISCONNECTED"?"TEMP":s.State=="DAILY_LOCK"?"DAILY LOCK":s.State=="COOLDOWN"?"COOLDOWN":"진입 대기";
            Detail="사유: "+Reason+"\n오늘 손실 "+DailyLosses+" / "+LossLimit+"회 | 연속손실 "+ConsecutiveLosses+" / "+ConsecutiveLimit+
                "회\n쿨다운 남은 시간: "+CooldownLeft+"\n일일 기준: "+DayZone+" / "+DayStartMinutes+"분\n연결명: "+s.Connection;
            foreach(string n in new[]{"Connection","TodayPnL","OpenPositions","DailyLosses","ConsecutiveLosses","CooldownLeft","State","Reason","Detail"})Changed(n);
        }
    }
    internal sealed class OzAccountChoice12
    {
        internal Account Value;
        internal string Name { get{return Value.Name;} }
        public override string ToString(){return Value.Name+" @ "+Oz11.ConnectionName(Value);}
    }
    public sealed class OzMultiAccountWindow : NTWindow,IWorkspacePersistence
    {
        private static readonly object instanceGate=new object();
        private static OzMultiAccountWindow instance;
        private readonly System.Collections.ObjectModel.ObservableCollection<OzAccountRow> rows=new System.Collections.ObjectModel.ObservableCollection<OzAccountRow>();
        private ComboBox selectAccount;
        private TextBox streamBox,loginBox,serverBox,mnqBox,mgcBox,dayZoneBox,dayStartBox;
        private TextBlock status,hint;
        private CheckBox allUse;
        private DataGrid grid;
        private OzReceiverHub hub;
        private string runningStream;
        private string appliedMnq="",appliedMgc="";
        private readonly System.Windows.Threading.DispatcherTimer timer;
        private bool closing,allowClose,updatingAll,updatingContracts;
        public static void Open()
        {
            lock(instanceGate)
            {
                if(instance!=null){instance.Dispatcher.InvokeAsync(()=>instance.Activate());return;}
                instance=new OzMultiAccountWindow();instance.Show();
            }
        }
        public OzMultiAccountWindow()
        {
            Caption="OZ Multi-Account Receiver — 계좌 관리";Width=1320;Height=620;MinWidth=950;MinHeight=360;Build();
            timer=new System.Windows.Threading.DispatcherTimer{Interval=TimeSpan.FromMilliseconds(500)};
            timer.Tick+=(s,e)=>Refresh();timer.Start();Closing+=ClosingWindow;
            Closed+=(s,e)=>{timer.Stop();lock(instanceGate)if(ReferenceEquals(instance,this))instance=null;};
        }
        private TextBox Field(Panel p,string name,string value,int width)
        {
            p.Children.Add(new Label{Content=name});var t=new TextBox{Text=value,Width=width,Margin=new Thickness(3),VerticalContentAlignment=VerticalAlignment.Center};p.Children.Add(t);return t;
        }
        private Button Button(Panel p,string text,RoutedEventHandler action)
        {
            var b=new Button{Content=text,Padding=new Thickness(10,5,10,5),Margin=new Thickness(4)};b.Click+=action;p.Children.Add(b);return b;
        }
        private void Build()
        {
            var root=new DockPanel{Margin=new Thickness(12)};
            var top=new StackPanel();DockPanel.SetDock(top,Dock.Top);root.Children.Add(top);
            var common=new WrapPanel();top.Children.Add(common);
            common.Children.Add(new Label{Content="OZ Multi-Account Receiver",FontSize=17,FontWeight=FontWeights.Bold,Margin=new Thickness(0,0,18,0)});
            appliedMnq="MNQ 12-26";appliedMgc="MGC 12-26";
            mnqBox=Field(common,"MNQ 월물",appliedMnq,110);mgcBox=Field(common,"MGC 월물",appliedMgc,110);
            mnqBox.LostKeyboardFocus+=(s,e)=>CommitContracts();mgcBox.LostKeyboardFocus+=(s,e)=>CommitContracts();
            var advanced=new Expander{Header="고급 연결 / 일일 기준 설정",IsExpanded=false,Margin=new Thickness(4)};
            var options=new WrapPanel();advanced.Content=options;top.Children.Add(advanced);
            streamBox=Field(options,"스트림","OZ_MAIN",100);loginBox=Field(options,"마스터 계좌 (0=최초 고정)","0",105);serverBox=Field(options,"서버","",140);
            dayZoneBox=Field(options,"새 계좌 시간대",TimeZoneInfo.Local.Id,175);dayStartBox=Field(options,"하루 시작 (분)","0",55);
            var actions=new WrapPanel();top.Children.Add(actions);
            selectAccount=new ComboBox{Width=235,Margin=new Thickness(4)};actions.Children.Add(selectAccount);ReloadAccounts();
            Button(actions,"계좌 새로고침",(s,e)=>ReloadAccounts());Button(actions,"+ 계좌 추가",(s,e)=>AddAccount());
            Button(actions,"+ 모든 계좌 추가",(s,e)=>AddAllAccounts());Button(actions,"선택 계좌 삭제",(s,e)=>RemoveSelected());Button(actions,"전체계좌 삭제",(s,e)=>RemoveAllAccounts());
            allUse=new CheckBox{Content="전체 사용",IsThreeState=false,Margin=new Thickness(12,8,4,4),VerticalAlignment=VerticalAlignment.Center,IsEnabled=false};
            // Click is USER intent only. Programmatic IsChecked changes never cascade to child rows.
            allUse.Click+=(s,e)=>SetAllEnabled(allUse.IsChecked==true);actions.Children.Add(allUse);
            status=new TextBlock{Text="마스터: 대기 | 준비됨 0 / 0",Margin=new Thickness(4),FontSize=15,FontWeight=FontWeights.Bold};top.Children.Add(status);
            hint=new TextBlock{Text="사용 체크는 즉시 반영됩니다. 숫자는 Enter / 다른 셀 이동 시 반영됩니다. 복구는 MT5 마스터의 Recovery를 누르세요.",Margin=new Thickness(4),TextWrapping=TextWrapping.Wrap};top.Children.Add(hint);
            grid=new DataGrid{ItemsSource=rows,AutoGenerateColumns=false,CanUserAddRows=false,CanUserDeleteRows=false,SelectionMode=DataGridSelectionMode.Single,
                RowHeight=34,ColumnHeaderHeight=36,EnableRowVirtualization=true,EnableColumnVirtualization=true};
            var check=new FrameworkElementFactory(typeof(CheckBox));
            check.SetValue(CheckBox.HorizontalAlignmentProperty,HorizontalAlignment.Center);check.SetValue(CheckBox.VerticalAlignmentProperty,VerticalAlignment.Center);
            check.SetBinding(CheckBox.IsCheckedProperty,new System.Windows.Data.Binding("Enable"){Mode=System.Windows.Data.BindingMode.TwoWay,UpdateSourceTrigger=System.Windows.Data.UpdateSourceTrigger.PropertyChanged});
            grid.Columns.Add(new DataGridTemplateColumn{Header="사용",CellTemplate=new DataTemplate{VisualTree=check},Width=50,IsReadOnly=true});
            AddColumn("계좌","Account",145,true);AddColumn("연결","Connection",85,true);
            AddColumn("Risk $","Risk",75,false);AddColumn("일손실 %","DailyMaxLoss",75,false);AddColumn("최대포지션","MaxPositions",85,false);
            AddColumn("손실한도(회)","LossLimit",90,false);AddColumn("연속손실(회)","ConsecutiveLimit",95,false);AddColumn("쿨다운(분)","CooldownMinutes",90,false);
            AddColumn("금일손익","TodayPnL",90,true);AddColumn("오픈","OpenPositions",55,true);AddColumn("상태","State",125,true);
            var rowStyle=new Style(typeof(DataGridRow));rowStyle.Setters.Add(new Setter(FrameworkElement.ToolTipProperty,new System.Windows.Data.Binding("Detail")));grid.RowStyle=rowStyle;
            grid.BeginningEdit+=(s,e)=>{var r=e.Row.Item as OzAccountRow;if(r!=null){r.Editing=true;r.UiError="";r.Engine.BeginSettingsEdit();}};
            grid.CellEditEnding+=CellEditEnding;
            root.Children.Add(grid);Content=root;
        }
        private void AddColumn(string title,string path,int width,bool readOnly)
        {
            grid.Columns.Add(new DataGridTextColumn{Header=title,Binding=new System.Windows.Data.Binding(path){Mode=readOnly?System.Windows.Data.BindingMode.OneWay:System.Windows.Data.BindingMode.TwoWay,
                UpdateSourceTrigger=System.Windows.Data.UpdateSourceTrigger.LostFocus,ValidatesOnExceptions=true},Width=width,IsReadOnly=readOnly});
        }
        private void ReloadAccounts()
        {
            var chosen=selectAccount.SelectedItem as OzAccountChoice12;Account[] list;lock(Account.All)list=Account.All.ToArray();
            selectAccount.Items.Clear();
            foreach(var a in list)selectAccount.Items.Add(new OzAccountChoice12{Value=a});
            var old=selectAccount.Items.Cast<OzAccountChoice12>().FirstOrDefault(c=>chosen!=null&&ReferenceEquals(c.Value,chosen.Value));
            if(old!=null)selectAccount.SelectedItem=old;else if(selectAccount.Items.Count>0)selectAccount.SelectedIndex=0;
        }
        private void AddAccount()
        {
            var choice=selectAccount.SelectedItem as OzAccountChoice12;if(choice==null)return;
            try{AddAccount(choice.Value);hint.Text="계좌가 추가되었습니다. 리스크를 확인한 뒤 사용을 체크하세요.";}
            catch(Exception ex){ShowError(ex.Message);}
        }
        private void AddAccount(Account account)
        {
            if(rows.Count>=20)throw new InvalidOperationException("최대 20계좌입니다.");
            if(OzSelection12.ContainsAccount(rows.Select(r=>r.Account),account.Name))throw new InvalidOperationException("이미 등록된 계좌입니다.");
            string key=account.Name+"@"+Oz11.ConnectionName(account);
            lock(Account.All)if(!Account.All.Any(a=>ReferenceEquals(a,account))||Account.All.Count(a=>string.Equals(a.Name,account.Name,StringComparison.OrdinalIgnoreCase))!=1)
                throw new InvalidOperationException("계좌 이름을 유일하게 확인할 수 없습니다: "+account.Name);
            int dayStart;if(!int.TryParse(dayStartBox.Text.Trim(),out dayStart))throw new ArgumentException("하루 시작 시간을 분 단위로 입력해 주세요.");
            var defaults=new OzAccountSettings{DayZone=dayZoneBox.Text.Trim(),DayStartMinutes=dayStart,Mnq=mnqBox.Text.Trim(),Mgc=mgcBox.Text.Trim()};defaults.Validate();
            if(hub==null)
            {
                ulong login;if(!ulong.TryParse(loginBox.Text.Trim(),out login))throw new ArgumentException("마스터 계좌 번호를 확인해 주세요.");
                runningStream=streamBox.Text.Trim();hub=new OzReceiverHub(runningStream,login,serverBox.Text.Trim());
                streamBox.IsEnabled=false;loginBox.IsEnabled=false;serverBox.IsEnabled=false;
            }
            var config=OzAccountEngine.LoadSettings(key,runningStream,defaults);config.Enabled=false;config.Mnq=mnqBox.Text.Trim();config.Mgc=mgcBox.Text.Trim();config.Validate();
            var engine=new OzAccountEngine(account,config,runningStream);
            try{hub.Add(engine);}catch{engine.Dispose();throw;}
            var row=new OzAccountRow{Engine=engine,Account=account.Name,Connection="확인 중",SelectionChanged=RefreshAllSelection};row.LoadValues(config);rows.Add(row);RefreshAllSelection();
        }
        private void AddAllAccounts()
        {
            ReloadAccounts();int added=0,duplicate=0,failed=0,limit=0;
            foreach(var choice in selectAccount.Items.Cast<OzAccountChoice12>().ToArray())
            {
                if(OzSelection12.ContainsAccount(rows.Select(r=>r.Account),choice.Name)){duplicate++;continue;}
                if(rows.Count>=20){limit++;continue;}
                try{AddAccount(choice.Value);added++;}catch(Exception ex){failed++;Oz11.Log("[계좌 추가 실패] "+choice.Name+" | "+ex.Message);}
            }
            hint.Text=String.Format("추가 {0}개 / 기존 {1}개 / 실패 {2}개 / 20계좌 초과 {3}개. 추가 계좌는 사용 해제 상태입니다.",added,duplicate,failed,limit);RefreshAllSelection();
        }
        private void SetAllEnabled(bool value)
        {
            if(updatingAll)return;updatingAll=true;
            try{foreach(var r in rows)r.Enable=value;}finally{updatingAll=false;RefreshAllSelection();}
        }
        private void RefreshAllSelection()
        {
            if(allUse==null||updatingAll)return;
            allUse.IsEnabled=rows.Count>0;allUse.IsChecked=OzSelection12.All(rows.Select(r=>r.Enable));
        }
        private void CellEditEnding(object sender,DataGridCellEditEndingEventArgs e)
        {
            var row=e.Row.Item as OzAccountRow;if(row==null)return;
            if(e.EditAction==DataGridEditAction.Cancel)
            {row.Editing=false;row.UiError="";row.Engine.CancelSettingsEdit();row.LoadValues(row.PendingSettings??row.Engine.Settings);return;}
            var box=e.EditingElement as TextBox;
            if(box!=null)
            {
                var binding=box.GetBindingExpression(TextBox.TextProperty);if(binding!=null)binding.UpdateSource();
                if(Validation.GetHasError(box)){e.Cancel=true;row.UiError="숫자를 확인해 주세요. 수정 중 신규 진입은 보류됩니다.";hint.Text=row.UiError;return;}
            }
            try
            {
                var next=RowSettings(row);next.Validate();ApplyRow(row,next);row.Editing=false;row.Engine.CancelSettingsEdit();
            }
            catch(Exception ex){e.Cancel=true;row.UiError=ex.Message;hint.Text="설정 오류: "+ex.Message+" (수정 또는 Esc)";}
        }
        private OzAccountSettings RowSettings(OzAccountRow r)
        {
            var next=r.Engine.Settings;next.Risk=r.Risk;next.DailyLossPercent=r.DailyMaxLoss;next.MaxPositions=r.MaxPositions;
            next.DailyLossTrades=r.LossLimit;next.ConsecutiveLosses=r.ConsecutiveLimit;next.CooldownMinutes=r.CooldownMinutes;return next;
        }
        private void ApplyRow(OzAccountRow row,OzAccountSettings next)
        {
            row.Applying=true;row.PendingSettings=next.Copy();row.UiError="";int revision=++row.ApplyRevision;
            row.Engine.Apply(next,error=>Dispatcher.InvokeAsync(()=>
            {
                if(revision!=row.ApplyRevision)return;
                row.Applying=false;row.PendingSettings=null;
                if(error!=""){row.UiError=error;if(!row.Editing)row.LoadValues(row.Engine.Settings);ShowError(row.Account+": "+error);}
                else hint.Text=row.Account+" 설정 반영 완료. 사용 체크는 별도 적용 없이 즉시 반영됩니다.";
                row.Refresh();RefreshAllSelection();
            }));
        }
        private void CommitContracts()
        {
            if(updatingContracts)return;
            string mnq=mnqBox.Text.Trim(),mgc=mgcBox.Text.Trim();if(mnq==appliedMnq&&mgc==appliedMgc)return;
            try
            {
                var check=new OzAccountSettings{Mnq=mnq,Mgc=mgc};check.Validate();
                if(rows.Any(r=>r.Editing||r.Applying))throw new InvalidOperationException("계좌 설정 반영이 끝난 뒤 월물을 변경해 주세요.");
                foreach(var r in rows)
                {var s=r.Engine.Status;if(s==null||!s.CanStop||!Oz11.Fresh(OzMasterReader.Tick(),s.Tick))throw new InvalidOperationException("포지션·주문이 있거나 상태를 확인 중인 계좌의 월물은 바꾸지 않습니다.");}
                appliedMnq=mnq;appliedMgc=mgc;
                foreach(var r in rows){var next=r.Engine.Settings;next.Mnq=mnq;next.Mgc=mgc;ApplyRow(r,next);}
            }
            catch(Exception ex){updatingContracts=true;mnqBox.Text=appliedMnq;mgcBox.Text=appliedMgc;updatingContracts=false;ShowError(ex.Message);}
        }
        private void ShowError(string message){if(hint!=null)hint.Text=message;Oz11.Burst("설정 확인",message,"NINJA UI");}
        private void RemoveSelected()
        {
            var r=grid.SelectedItem as OzAccountRow;if(r==null)return;
            r.Engine.StopIfFlat(ok=>Dispatcher.InvokeAsync(()=>
            {if(ok){hub.Remove(r.Engine);rows.Remove(r);RefreshAllSelection();}else ShowError("포지션·미체결·청산 목표가 남아 있어 삭제하지 않았습니다: "+r.Account);}));
        }
        private void RemoveAllAccounts()
        {
            var targets=rows.ToArray();if(targets.Length==0)return;
            int pending=targets.Length,removed=0;var blocked=new List<string>();
            foreach(var r in targets)r.Engine.StopIfFlat(ok=>Dispatcher.InvokeAsync(()=>
            {
                if(ok){hub.Remove(r.Engine);rows.Remove(r);removed++;}else blocked.Add(r.Account);
                pending--;if(pending!=0)return;RefreshAllSelection();
                if(blocked.Count==0)hint.Text="전체 계좌 삭제 완료: "+removed+"개";
                else ShowError("삭제 "+removed+"개 / 유지 "+blocked.Count+"개. 포지션·미체결·청산 목표가 남아 있는 계좌: "+string.Join(", ",blocked));
            }));
        }
        private void Refresh()
        {
            foreach(var r in rows)r.Refresh();RefreshAllSelection();ulong now=OzMasterReader.Tick();
            int enabled=rows.Count(r=>r.Engine.RequestedEnabled);
            int ready=rows.Count(r=>r.Engine.RequestedEnabled&&r.Engine.Status!=null&&r.Engine.Status.EntryReady&&Oz11.Fresh(now,r.Engine.Status.Tick)&&!r.Editing&&!r.Applying);
            var p=hub==null?null:hub.Current;
            string master=p==null?"대기":p.State==null||!Oz11.Fresh(now,p.State.Alive)?"연결 끊김":p.Link==OzCommState.RUNNING?"연결됨":p.Link==OzCommState.HARD_HOLD?"확인 필요":"복구 중";
            status.Text="마스터: "+master+"  |  준비됨 "+ready+" / "+enabled+"  |  등록 "+rows.Count+" / 20";
        }
        private void ClosingWindow(object sender,System.ComponentModel.CancelEventArgs e)
        {
            if(allowClose){if(hub!=null)hub.Dispose();return;}
            if(rows.Count==0){allowClose=true;if(hub!=null)hub.Dispose();return;}
            e.Cancel=true;if(closing)return;
            if(rows.Any(r=>r.Engine.Status==null||!r.Engine.Status.CanStop))
            {ShowError("포지션·미체결·청산 목표가 남아 있어 수신창을 유지합니다.");return;}
            closing=true;int pending=rows.Count;bool all=true;
            foreach(var r in rows.ToArray())r.Engine.StopIfFlat(ok=>Dispatcher.InvokeAsync(()=>
            {
                all&=ok;if(ok){hub.Remove(r.Engine);rows.Remove(r);}
                pending--;if(pending!=0)return;closing=false;RefreshAllSelection();
                if(all){allowClose=true;Close();}else ShowError("새 주문이 감지되어 창 종료를 보류했습니다.");
            }));
        }
        public WorkspaceOptions WorkspaceOptions{get;set;}
        public void Restore(System.Xml.Linq.XDocument document,System.Xml.Linq.XElement element) { /* Re-register accounts after process restart; never auto-enable trading. */ }
        public void Save(System.Xml.Linq.XDocument document,System.Xml.Linq.XElement element) { /* Settings/risk persistence is separate from the workspace. */ }
    }

}
